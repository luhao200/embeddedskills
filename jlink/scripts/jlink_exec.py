"""J-Link 设备探测、烧录、内存读写、寄存器查看、复位、在线调试"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# 添加 runtime 模块路径
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from jlink_runtime import (
    load_local_config,
    load_project_config,
    resolve_project_param,
    resolve_runtime_param,
    resolve_tool_param,
    save_project_config,
    load_workspace_state,
    get_state_entry,
    update_state_entry,
    workspace_root,
    normalize_path,
    hidden_subprocess_kwargs,
    is_missing,
)

# J-Link Commander 命令模板
TEMPLATES = {
    "info": "si {interface}\nspeed {speed}\nconnect\nsleep 200\nexit\n",
    "flash_hex": "si {interface}\nspeed {speed}\nconnect\nloadfile {file}\nr\ng\nexit\n",
    "flash_bin": "si {interface}\nspeed {speed}\nconnect\nloadbin {file},{address}\nr\ng\nexit\n",
    "read_mem": "si {interface}\nspeed {speed}\nconnect\nhalt\nmem{width} {address},{length}\nexit\n",
    "write_mem": "si {interface}\nspeed {speed}\nconnect\nhalt\nw{width} {address},{value}\nexit\n",
    "regs": "si {interface}\nspeed {speed}\nconnect\nhalt\nregs\nexit\n",
    "reset": "si {interface}\nspeed {speed}\nconnect\nr\ng\nexit\n",
    "halt": "si {interface}\nspeed {speed}\nconnect\nhalt\nregs\nexit\n",
    "go": "si {interface}\nspeed {speed}\nconnect\ng\nexit\n",
    "step": "si {interface}\nspeed {speed}\nconnect\nhalt\n{step_commands}regs\nexit\n",
    "run_to": "si {interface}\nspeed {speed}\nconnect\nSetBP {address}\ng\nsleep {timeout_ms}\nhalt\nregs\nexit\n",
}

# 错误模式匹配
ERROR_PATTERNS = [
    (r"Cannot connect to target", "cannot_connect_target", "无法连接目标芯片，请检查连线、供电和接口类型"),
    (r"Could not find core", "core_not_found", "未找到内核，请确认 device 是否匹配目标芯片"),
    (r"No J-Link found", "no_jlink_found", "未检测到 J-Link 探针，请确认 USB 连接和驱动"),
    (r"Multiple J-Links found", "multiple_jlinks", "检测到多个 J-Link 探针，请通过 --serial-no 指定序列号"),
    (r"Could not open file", "file_not_found", "无法打开固件文件，请确认路径正确"),
    (r"Unknown device", "unknown_device", "未知芯片型号，请确认 --device 参数"),
    (r"VTarget too low", "vtarget_low", "目标电压过低，请检查目标板供电"),
]


def build_jlink_cmd(exe: str, device: str, script_path: str, serial_no: str = "") -> list:
    """构建 JLink.exe 命令行"""
    cmd = [exe, "-NoGui", "1", "-ExitOnError", "1", "-AutoConnect", "1"]
    cmd.extend(["-Device", device])
    if serial_no:
        cmd.extend(["-SelectEmuBySN", serial_no])
    cmd.extend(["-CommandFile", script_path])
    return cmd


def parse_registers(stdout: str) -> dict:
    """从 JLink 输出中解析寄存器值"""
    registers = {}
    # 匹配 "REG = HEXVALUE" 或 "REG= HEXVALUE" 格式
    reg_lines = re.findall(r"(\w+)\s*=\s*([0-9A-Fa-f]{8})", stdout)
    if reg_lines:
        registers = {name: f"0x{val}" for name, val in reg_lines}
    return registers


def parse_pc(stdout: str) -> str:
    """从输出中提取 PC 值"""
    m = re.search(r"PC\s*=\s*([0-9A-Fa-f]{8})", stdout)
    return f"0x{m.group(1)}" if m else ""


def parse_output(stdout: str, action: str) -> dict:
    """解析 JLink.exe 输出，提取关键信息"""
    result = {"raw": stdout}

    # 检查错误模式
    for pattern, code, message in ERROR_PATTERNS:
        if re.search(pattern, stdout, re.IGNORECASE):
            return {"error_code": code, "error_message": message, "raw": stdout}

    # info: 提取固件版本和目标信息
    if action == "info":
        fw = re.search(r"Firmware:\s+(.+)", stdout)
        sn = re.search(r"S/N:\s+(\d+)", stdout)
        vtarget = re.search(r"VTref=(\d+\.\d+)V", stdout)
        device_match = re.search(r"Device \"(.+?)\" selected", stdout)
        if fw:
            result["firmware"] = fw.group(1).strip()
        if sn:
            result["serial_no"] = sn.group(1).strip()
        if vtarget:
            result["vtarget_v"] = float(vtarget.group(1))
        if device_match:
            result["device"] = device_match.group(1)

    # flash: 提取烧录信息
    elif action == "flash":
        speed = re.search(r"Downloading\s+\d+\s+bytes?\s.*?(\d+\.\d+)\s*KB/s", stdout)
        if speed:
            result["speed_kbps"] = float(speed.group(1))
        if "O.K." in stdout or "Verify successful" in stdout or "Download verified successfully" in stdout:
            result["verified"] = True

    # read-mem: 提取内存数据
    elif action == "read-mem":
        mem_lines = re.findall(r"^([0-9A-Fa-f]{8}) = (.+)$", stdout, re.MULTILINE)
        if mem_lines:
            result["memory"] = []
            for addr, data in mem_lines:
                cleaned = data.strip()
                result["memory"].append({"address": f"0x{addr}", "data": cleaned})

    # regs / halt: 提取寄存器值
    elif action in ("regs", "halt"):
        regs = parse_registers(stdout)
        if regs:
            result["registers"] = regs

    # step: 提取执行的指令和寄存器
    elif action == "step":
        # 匹配 step 输出: ADDR: OPCODE INSTRUCTION
        instructions = re.findall(
            r"^([0-9A-Fa-f]{8}):\s+([0-9A-Fa-f ]+?)\s{2,}(.+)$", stdout, re.MULTILINE
        )
        if instructions:
            result["steps"] = []
            for addr, opcode, instr in instructions:
                result["steps"].append({
                    "address": f"0x{addr}",
                    "opcode": opcode.strip(),
                    "instruction": instr.strip(),
                })
        regs = parse_registers(stdout)
        if regs:
            result["registers"] = regs

    # run-to: 提取断点命中状态和寄存器
    elif action == "run-to":
        m = re.search(r"Breakpoint set @ addr 0x([0-9A-Fa-f]+)\s*\(Handle = (\d+)\)", stdout)
        if m:
            result["bp_address"] = f"0x{m.group(1)}"
            result["bp_handle"] = int(m.group(2))
        elif "Could not set" in stdout:
            return {"error_code": "bp_set_failed", "error_message": "断点设置失败，可能硬件断点槽已满", "raw": stdout}
        regs = parse_registers(stdout)
        if regs:
            result["registers"] = regs
        # 判断是否命中断点（PC == 断点地址）
        pc = parse_pc(stdout)
        if m and pc:
            bp_addr = f"0x{m.group(1)}"
            result["bp_hit"] = pc.upper() == bp_addr.upper()

    return result


def run_jlink(exe: str, device: str, action: str, interface: str = "SWD",
              speed: str = "4000", serial_no: str = "", file: str = "",
              address: str = "", length: str = "256", value: str = "",
              width: str = "32", step_count: int = 1,
              timeout_ms: str = "2000") -> dict:
    """执行 JLink Commander 命令"""
    start_time = time.time()

    # 选择模板
    if action == "flash":
        if file.lower().endswith(".bin"):
            if not address:
                return {
                    "status": "error",
                    "action": action,
                    "error": {"code": "missing_address", "message": ".bin 文件必须提供 --address 烧录地址"},
                }
            template = TEMPLATES["flash_bin"]
        else:
            template = TEMPLATES["flash_hex"]
    else:
        template_key = action.replace("-", "_")
        if template_key in TEMPLATES:
            template = TEMPLATES[template_key]
        else:
            return {
                "status": "error",
                "action": action,
                "error": {"code": "unknown_action", "message": f"未知子命令: {action}"},
            }

    # width 映射
    width_map = {"8": "8", "16": "16", "32": "32"}
    w = width_map.get(width, "32")

    # step 命令: 生成多条 step 指令
    step_commands = ""
    if action == "step":
        step_commands = "".join(["step\n" for _ in range(step_count)])

    # 渲染命令脚本
    script_content = template.format(
        interface=interface, speed=speed, file=file,
        address=address, length=length, value=value, width=w,
        step_commands=step_commands, timeout_ms=timeout_ms,
    )

    # 写入临时文件
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jlink", delete=False, encoding="utf-8") as f:
        f.write(script_content)
        script_path = f.name

    try:
        if not os.path.isfile(exe):
            return {
                "status": "error",
                "action": action,
                "error": {"code": "exe_not_found", "message": f"JLink.exe 不存在: {exe}"},
            }

        if file and not os.path.isfile(file):
            return {
                "status": "error",
                "action": action,
                "error": {"code": "file_not_found", "message": f"固件文件不存在: {file}"},
            }

        cmd = build_jlink_cmd(exe, device, script_path, serial_no)

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
                **hidden_subprocess_kwargs()
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "action": action,
                "error": {"code": "timeout", "message": "JLink.exe 执行超时(120s)"},
            }
        except Exception as e:
            return {
                "status": "error",
                "action": action,
                "error": {"code": "exec_error", "message": str(e)},
            }

        elapsed_ms = int((time.time() - start_time) * 1000)
        parsed = parse_output(proc.stdout, action)

        if "error_code" in parsed:
            return {
                "status": "error",
                "action": action,
                "error": {"code": parsed["error_code"], "message": parsed["error_message"]},
                "details": {"device": device, "elapsed_ms": elapsed_ms, "errorlevel": proc.returncode},
            }

        # 构建摘要
        summary_map = {
            "info": "探测成功",
            "flash": "烧录成功",
            "read-mem": "内存读取成功",
            "write-mem": "内存写入成功",
            "regs": "寄存器读取成功",
            "reset": "复位成功",
            "halt": f"已暂停，PC={parse_pc(proc.stdout)}",
            "go": "已恢复运行",
            "step": f"单步{step_count}次，PC={parse_pc(proc.stdout)}",
            "run-to": f"运行至断点，PC={parse_pc(proc.stdout)}",
        }
        summary = summary_map.get(action, "执行成功")

        # 补充 run-to 摘要
        if action == "run-to" and "bp_hit" in parsed:
            if parsed["bp_hit"]:
                summary = f"断点命中 @ {parsed['bp_address']}，PC={parse_pc(proc.stdout)}"
            else:
                summary = f"超时未命中断点 @ {parsed.get('bp_address', '?')}，当前 PC={parse_pc(proc.stdout)}"

        details = {
            "device": device,
            "interface": interface,
            "speed_khz": int(speed),
            "elapsed_ms": elapsed_ms,
            "errorlevel": proc.returncode,
        }
        if serial_no:
            details["serial_no"] = serial_no

        # 合并解析结果
        for k, v in parsed.items():
            if k != "raw":
                details[k] = v

        # 判断状态: returncode!=0 可能只是警告，需结合输出判断
        if proc.returncode != 0 and "error_code" not in parsed:
            if action == "flash" and parsed.get("verified"):
                status = "ok"
            else:
                status = "error"
                summary = f"执行返回非零退出码: {proc.returncode}"
        else:
            status = "ok"

        return {
            "status": status,
            "action": action,
            "summary": summary,
            "details": details,
        }
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


ALL_ACTIONS = [
    "info", "flash", "read-mem", "write-mem", "regs", "reset",
    "halt", "go", "step", "run-to",
]


def resolve_device_params(args):
    """解析 exe/device/interface/speed/serial_no 参数。

    优先级:
    - exe / serial_no: CLI > 环境级配置
    - device / interface / speed: CLI > 工程配置 > state.json > 默认值
    """
    workspace = workspace_root(args.workspace)
    local_config = load_local_config(__file__)
    project_config = load_project_config(str(workspace))
    state = load_workspace_state(str(workspace))

    # 从 state 获取历史值
    last_flash = get_state_entry(state, "last_flash")
    last_debug = get_state_entry(state, "last_debug")
    state_lookup = {
        "device": last_flash.get("device") or last_debug.get("device"),
        "interface": last_flash.get("interface") or last_debug.get("interface"),
        "speed": last_flash.get("speed") or last_debug.get("speed"),
        "serial_no": last_flash.get("serial_no") or last_debug.get("serial_no"),
    }

    device, device_source = resolve_project_param(
        "device",
        args.device,
        project_config=project_config,
        project_keys=["device"],
        state_record=state_lookup,
        state_keys=["device"],
    )
    interface, interface_source = resolve_project_param(
        "interface",
        args.interface,
        project_config=project_config,
        project_keys=["interface"],
        state_record=state_lookup,
        state_keys=["interface"],
        default="SWD",
    )
    speed, speed_source = resolve_project_param(
        "speed",
        args.speed,
        project_config=project_config,
        project_keys=["speed"],
        state_record=state_lookup,
        state_keys=["speed"],
        default="4000",
    )
    exe, exe_source = resolve_tool_param(
        "exe",
        args.exe,
        local_config=local_config,
        local_keys=["exe"],
        path_candidates=["JLink.exe"],
        default="JLink.exe",
    )
    serial_no, serial_no_source = resolve_runtime_param(
        "serial_no",
        args.serial_no,
        local_config=local_config,
        local_keys=["serial_no"],
        state_record=state_lookup,
        state_keys=["serial_no"],
    )

    return {
        "exe": exe,
        "exe_source": exe_source,
        "device": device,
        "device_source": device_source,
        "interface": interface,
        "interface_source": interface_source,
        "speed": speed,
        "speed_source": speed_source,
        "serial_no": serial_no,
        "serial_no_source": serial_no_source,
    }


def main():
    parser = argparse.ArgumentParser(description="J-Link 设备探测/烧录/内存读写/寄存器/复位/在线调试")
    parser.add_argument("action", choices=ALL_ACTIONS)
    parser.add_argument("--exe", default="", help="JLink.exe 路径")
    parser.add_argument("--device", default=None, help="芯片型号（如 STM32F407VG）")
    parser.add_argument("--interface", default=None, help="调试接口")
    parser.add_argument("--speed", default=None, help="调试速率 kHz")
    parser.add_argument("--serial-no", default="", help="探针序列号")
    parser.add_argument("--file", default="", help="固件文件路径（flash 用）")
    parser.add_argument("--address", default="", help="地址（flash .bin / read-mem / write-mem / bp-set 用）")
    parser.add_argument("--length", default="256", help="读取长度（read-mem 用）")
    parser.add_argument("--value", default="", help="写入值（write-mem 用）")
    parser.add_argument("--width", default="32", choices=["8", "16", "32"], help="数据宽度")
    parser.add_argument("--count", type=int, default=1, help="单步次数（step 用）")
    parser.add_argument("--timeout-ms", default="2000", help="run-to 等待断点命中的超时毫秒数")
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    # 解析参数
    params = resolve_device_params(args)
    workspace = workspace_root(args.workspace)

    # 检查 device 是否已提供
    if is_missing(params["device"]):
        result = {
            "status": "error", "action": args.action,
            "error": {"code": "missing_device", "message": "必须提供 --device 芯片型号，或通过 .embeddedskills/config.json 配置"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    # 参数校验
    if args.action == "flash" and not args.file:
        result = {
            "status": "error", "action": "flash",
            "error": {"code": "missing_file", "message": "flash 必须提供 --file 固件文件路径"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    if args.action == "write-mem" and (not args.address or not args.value):
        result = {
            "status": "error", "action": "write-mem",
            "error": {"code": "missing_params", "message": "write-mem 必须提供 --address 和 --value"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    if args.action == "read-mem" and not args.address:
        result = {
            "status": "error", "action": "read-mem",
            "error": {"code": "missing_address", "message": "read-mem 必须提供 --address"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    if args.action == "run-to" and not args.address:
        result = {
            "status": "error", "action": "run-to",
            "error": {"code": "missing_address", "message": "run-to 必须提供 --address 断点地址"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    result = run_jlink(
        exe=params["exe"],
        device=params["device"],
        action=args.action,
        interface=params["interface"],
        speed=params["speed"],
        serial_no=params["serial_no"],
        file=args.file,
        address=args.address,
        length=args.length,
        value=args.value,
        width=args.width,
        step_count=args.count,
        timeout_ms=args.timeout_ms,
    )

    # 成功执行后，写回确认过的参数到工程配置
    if result.get("status") == "ok":
        save_project_config(str(workspace), {
            "device": params["device"],
            "interface": params["interface"],
            "speed": params["speed"],
        })
        # 同时更新 state.json
        if args.action in ("flash", "reset", "halt", "go", "step", "run-to", "info"):
            state_action = "last_flash" if args.action == "flash" else "last_debug"
            update_state_entry(
                state_action,
                {
                    "provider": "jlink",
                    "action": args.action,
                    "device": params["device"],
                    "interface": params["interface"],
                    "speed": params["speed"],
                    "serial_no": params["serial_no"] or "",
                },
                str(workspace),
            )

    if args.as_json:
        # 添加参数来源信息
        if "details" not in result:
            result["details"] = {}
        result["details"]["parameter_sources"] = {
            "exe": params["exe_source"],
            "device": params["device_source"],
            "interface": params["interface_source"],
            "speed": params["speed_source"],
            "serial_no": params["serial_no_source"],
        }
        output_json(result)
    else:
        if result["status"] == "ok":
            print(f"[{args.action}] {result.get('summary', '成功')}")
            details = result.get("details", {})
            if "registers" in details:
                # 只显示核心寄存器
                core_regs = ["PC", "R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7",
                             "R8", "R9", "R10", "R11", "R12", "MSP", "PSP", "XPSR"]
                for name in core_regs:
                    if name in details["registers"]:
                        print(f"  {name:>5s} = {details['registers'][name]}")
            if "steps" in details:
                for s in details["steps"]:
                    print(f"  {s['address']}: {s['opcode']:16s} {s['instruction']}")
            if "memory" in details:
                for m in details["memory"]:
                    print(f"  {m['address']}: {m['data']}")
            if "bp_hit" in details:
                hit = "命中" if details["bp_hit"] else "未命中（超时）"
                print(f"  断点: {details.get('bp_address', '?')} — {hit}")
        else:
            err = result.get("error", {})
            print(f"[{args.action}] 失败 — {err.get('message', '未知错误')}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
