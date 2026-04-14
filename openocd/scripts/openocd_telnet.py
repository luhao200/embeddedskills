"""OpenOCD Telnet 调试命令

通过 socket 连接 OpenOCD Telnet 端口，执行在线调试命令：
halt / resume / step / reg / read-mem / write-mem / bp / rbp / run-to
"""

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

# 添加 runtime 模块路径
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from openocd_runtime import (
    hidden_subprocess_kwargs,
    load_project_config,
    save_project_config,
    load_workspace_state,
    get_state_entry,
    workspace_root,
    is_missing,
)


# ── OpenOCD 服务器启动（复用 openocd_gdb.py 模式） ──────────────────

def build_openocd_cmd(exe: str, board: str = "", interface: str = "", target: str = "",
                      search: str = "", adapter_speed: str = "", transport: str = "",
                      gdb_port: int = 3333, telnet_port: int = 4444) -> list:
    """构建 OpenOCD 命令行"""
    cmd = [exe]
    if search:
        cmd.extend(["-s", search])
    if board:
        cmd.extend(["-f", board])
    else:
        if interface:
            cmd.extend(["-f", interface])
        if target:
            cmd.extend(["-f", target])
    if adapter_speed:
        cmd.extend(["-c", f"adapter speed {adapter_speed}"])
    if transport:
        cmd.extend(["-c", f"transport select {transport}"])
    cmd.extend(["-c", f"gdb_port {gdb_port}"])
    cmd.extend(["-c", f"telnet_port {telnet_port}"])
    return cmd


def start_openocd_server(cmd: list) -> subprocess.Popen:
    """启动 OpenOCD 进程"""
    popen_kwargs = hidden_subprocess_kwargs()
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    if popen_kwargs.get("creationflags"):
        creationflags |= popen_kwargs["creationflags"]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        startupinfo=popen_kwargs.get("startupinfo"),
    )


def wait_server_ready(proc: subprocess.Popen, telnet_port: int, timeout: int = 15) -> tuple:
    """等待 OpenOCD 就绪，返回 (ready, errors)"""
    start = time.time()
    errors = []
    ready = False
    while time.time() - start < timeout:
        if proc.poll() is not None:
            remaining = proc.stderr.read()
            for line in remaining.splitlines():
                if "Error:" in line:
                    errors.append(line.strip())
            return False, errors
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.1)
            continue
        line = line.strip()
        if "Error:" in line:
            errors.append(line)
        if f"Listening on port {telnet_port}" in line or "listening on" in line.lower():
            ready = True
            break

    if not ready:
        return False, errors

    critical_keywords = [
        "open failed", "init mode failed", "no device found",
        "cannot connect", "error connecting dp", "examination failed",
        "failed to read memory", "failed to write memory",
        "cannot read idr", "polling failed",
    ]
    critical_errors = [e for e in errors if any(k in e.lower() for k in critical_keywords)]
    if critical_errors:
        return False, critical_errors
    return True, errors


def cleanup_proc(proc: subprocess.Popen):
    """清理 OpenOCD 进程"""
    if proc and proc.poll() is None:
        try:
            if sys.platform == "win32":
                proc.terminate()
            else:
                proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()


# ── Telnet 连接层 ──────────────────────────────────────────────

class TelnetConnection:
    """通过 raw socket 连接 OpenOCD Telnet 接口"""

    def __init__(self, host: str = "localhost", port: int = 4444, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self._buf = b""

    @staticmethod
    def _strip_iac(data: bytes) -> bytes:
        """过滤 Telnet IAC 协商字节和 NUL 字符"""
        clean = bytearray()
        i = 0
        while i < len(data):
            b = data[i]
            if b == 0xff and i + 2 < len(data):
                i += 3  # 跳过 IAC + cmd + option
            elif b == 0x00:
                i += 1  # 跳过 NUL
            else:
                clean.append(b)
                i += 1
        return bytes(clean)

    def connect(self, retries: int = 3, retry_delay: float = 0.5):
        """连接到 Telnet 端口，支持重试"""
        for i in range(retries):
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(self.timeout)
                self.sock.connect((self.host, self.port))
                # 读取初始提示符
                self._read_until_prompt()
                return
            except (ConnectionRefusedError, OSError):
                if self.sock:
                    self.sock.close()
                    self.sock = None
                if i < retries - 1:
                    time.sleep(retry_delay)
        raise ConnectionError(f"无法连接 OpenOCD Telnet 端口 {self.host}:{self.port}")

    def send(self, command: str) -> str:
        """发送命令并返回响应文本"""
        if not self.sock:
            raise ConnectionError("未连接")
        self.sock.sendall((command + "\n").encode("utf-8"))
        return self._read_until_prompt()

    def _read_until_prompt(self) -> str:
        """读取数据直到出现 '> ' 提示符"""
        while True:
            # 过滤 IAC 和 NUL，再解码
            clean = self._strip_iac(self._buf)
            decoded = clean.decode("utf-8", errors="replace")
            # OpenOCD 提示符: "\r\n> " 或 "\r> " 或末尾 "> "
            prompt_pos = decoded.rfind("\n> ")
            if prompt_pos == -1:
                prompt_pos = decoded.rfind("\r> ")
            if prompt_pos == -1 and decoded.endswith("> "):
                prompt_pos = len(decoded) - 2
            if prompt_pos >= 0:
                response = decoded[:prompt_pos]
                self._buf = b""
                # 去除命令回显（第一行通常是发送的命令本身）
                lines = response.split("\n")
                if lines:
                    lines = [l.rstrip("\r") for l in lines]
                return "\n".join(lines).strip()
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    clean = self._strip_iac(self._buf)
                    response = clean.decode("utf-8", errors="replace")
                    self._buf = b""
                    return response.strip()
                self._buf += chunk
            except socket.timeout:
                clean = self._strip_iac(self._buf)
                response = clean.decode("utf-8", errors="replace")
                self._buf = b""
                return response.strip()

    def close(self):
        if self.sock:
            try:
                self.sock.sendall(b"shutdown\n")
                time.sleep(0.2)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


# ── 输出解析 ──────────────────────────────────────────────────

def parse_reg_single(raw: str) -> dict:
    """解析单个寄存器查询结果: 'regname (/bits): 0xVALUE' 或 'regname 0xVALUE'"""
    result = {}
    # 格式1: pc (/32): 0x080009dc
    m = re.search(r"(\w+)\s+\(/\d+\):\s*(0x[0-9a-fA-F]+)", raw)
    if m:
        result[m.group(1)] = m.group(2)
        return result
    # 格式2: pc 0x080009dc（get_reg 格式）
    m = re.search(r"(\w+)\s+(0x[0-9a-fA-F]+)", raw)
    if m:
        result[m.group(1)] = m.group(2)
    return result


def parse_reg_response(raw: str) -> dict:
    """解析 reg 命令输出（全部寄存器）"""
    registers = {}
    # halt 后 reg 输出格式: (N) regname (/bits): 0xVALUE
    for m in re.finditer(r"\(\d+\)\s+(\S+)\s+\(/\d+\):\s*(0x[0-9a-fA-F]+)", raw):
        registers[m.group(1)] = m.group(2)
    # 备用格式: regname (/bits): 0xVALUE（无序号）
    if not registers:
        for m in re.finditer(r"(\w+)\s+\(/\d+\):\s*(0x[0-9a-fA-F]+)", raw):
            registers[m.group(1)] = m.group(2)
    return registers


def parse_mem_response(raw: str) -> list:
    """解析内存读取输出: 0xADDR: DATA DATA ..."""
    memory = []
    for m in re.finditer(r"(0x[0-9a-fA-F]+)\s*:\s*([0-9a-fA-F ]+)", raw):
        addr = m.group(1)
        data = m.group(2).strip()
        memory.append({"address": addr, "data": data})
    return memory


def has_command_error(raw: str) -> bool:
    """识别 OpenOCD Telnet 返回中的失败语义。"""
    lowered = raw.lower()
    keywords = [
        "error:",
        "failed",
        "not halted",
        "context restore failed",
        "target not halted",
        "timed out",
        "unknown command",
    ]
    return any(keyword in lowered for keyword in keywords)


# ── 主逻辑 ────────────────────────────────────────────────────

def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


ALL_ACTIONS = ["halt", "resume", "step", "reg", "read-mem", "write-mem", "bp", "rbp", "run-to"]


def resolve_openocd_params(args, project_config: dict, state_lookup: dict) -> dict:
    """解析 OpenOCD 工程级参数，优先级: CLI > 工程配置 > state.json"""
    # board: CLI > 工程配置 > state
    board = args.board
    board_source = "cli"
    if is_missing(board):
        board = project_config.get("board")
        board_source = "project_config"
    if is_missing(board):
        board = state_lookup.get("board")
        board_source = "state"

    # interface: CLI > 工程配置 > state
    interface = args.interface
    interface_source = "cli"
    if is_missing(interface):
        interface = project_config.get("interface")
        interface_source = "project_config"
    if is_missing(interface):
        interface = state_lookup.get("interface")
        interface_source = "state"

    # target: CLI > 工程配置 > state
    target = args.target
    target_source = "cli"
    if is_missing(target):
        target = project_config.get("target")
        target_source = "project_config"
    if is_missing(target):
        target = state_lookup.get("target")
        target_source = "state"

    # adapter_speed: CLI > 工程配置 > state
    adapter_speed = args.adapter_speed
    adapter_speed_source = "cli"
    if is_missing(adapter_speed):
        adapter_speed = project_config.get("adapter_speed")
        adapter_speed_source = "project_config"
    if is_missing(adapter_speed):
        adapter_speed = state_lookup.get("adapter_speed")
        adapter_speed_source = "state"

    # transport: CLI > 工程配置 > state
    transport = args.transport
    transport_source = "cli"
    if is_missing(transport):
        transport = project_config.get("transport")
        transport_source = "project_config"
    if is_missing(transport):
        transport = state_lookup.get("transport")
        transport_source = "state"

    return {
        "board": board,
        "board_source": board_source,
        "interface": interface,
        "interface_source": interface_source,
        "target": target,
        "target_source": target_source,
        "adapter_speed": adapter_speed,
        "adapter_speed_source": adapter_speed_source,
        "transport": transport,
        "transport_source": transport_source,
    }


def main():
    parser = argparse.ArgumentParser(description="OpenOCD Telnet 调试命令")
    parser.add_argument("action", choices=ALL_ACTIONS)
    parser.add_argument("--exe", default="openocd", help="openocd 路径")
    parser.add_argument("--board", default=None, help="board 配置文件")
    parser.add_argument("--interface", default=None, help="interface 配置文件")
    parser.add_argument("--target", default=None, help="target 配置文件")
    parser.add_argument("--search", default="", help="额外配置脚本搜索目录")
    parser.add_argument("--adapter-speed", default=None, help="调试速率 kHz")
    parser.add_argument("--transport", default=None, choices=["", "swd", "jtag"], help="传输协议")
    parser.add_argument("--gdb-port", type=int, default=3333, help="GDB 端口")
    parser.add_argument("--telnet-port", type=int, default=4444, help="Telnet 端口")
    parser.add_argument("--address", default="", help="地址（read-mem/write-mem/bp/rbp/run-to 用）")
    parser.add_argument("--length", type=int, default=16, help="读取长度（read-mem 用，单位为 width 数量）")
    parser.add_argument("--value", default="", help="写入值（write-mem 用）")
    parser.add_argument("--width", default="32", choices=["8", "16", "32"], help="数据宽度")
    parser.add_argument("--count", type=int, default=1, help="单步次数（step 用）")
    parser.add_argument("--timeout-ms", type=int, default=2000, help="run-to 等待超时毫秒数")
    parser.add_argument("--bp-length", type=int, default=2, help="断点长度（bp 用，Thumb=2/ARM=4）")
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    # 解析工程级参数
    workspace = workspace_root(args.workspace)
    project_config = load_project_config(str(workspace))
    state = load_workspace_state(str(workspace))
    state_lookup = {
        "board": get_state_entry(state, "last_debug").get("board") or get_state_entry(state, "last_flash").get("board"),
        "interface": get_state_entry(state, "last_debug").get("interface") or get_state_entry(state, "last_flash").get("interface"),
        "target": get_state_entry(state, "last_debug").get("target") or get_state_entry(state, "last_flash").get("target"),
        "adapter_speed": get_state_entry(state, "last_debug").get("adapter_speed") or get_state_entry(state, "last_flash").get("adapter_speed"),
        "transport": get_state_entry(state, "last_debug").get("transport") or get_state_entry(state, "last_flash").get("transport"),
    }
    oc_params = resolve_openocd_params(args, project_config, state_lookup)

    # 使用解析后的参数
    board = oc_params["board"]
    interface = oc_params["interface"]
    target = oc_params["target"]
    adapter_speed = oc_params["adapter_speed"]
    transport = oc_params["transport"]

    # 参数校验
    if not board and not interface and not target:
        result = {
            "status": "error", "action": args.action,
            "error": {"code": "missing_config", "message": "必须提供 --board 或 --interface + --target，或通过 .embeddedskills/config.json 配置"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr, flush=True)
        sys.exit(1)

    if args.action in ("read-mem", "write-mem", "bp", "rbp", "run-to") and not args.address:
        result = {
            "status": "error", "action": args.action,
            "error": {"code": "missing_address", "message": f"{args.action} 必须提供 --address"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr, flush=True)
        sys.exit(1)

    if args.action == "write-mem" and not args.value:
        result = {
            "status": "error", "action": args.action,
            "error": {"code": "missing_value", "message": "write-mem 必须提供 --value"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr, flush=True)
        sys.exit(1)

    # 构建 OpenOCD 命令并启动
    cmd = build_openocd_cmd(
        exe=args.exe, board=board or "", interface=interface or "", target=target or "",
        search=args.search, adapter_speed=adapter_speed or "", transport=transport or "",
        gdb_port=args.gdb_port, telnet_port=args.telnet_port,
    )

    proc = None
    telnet = None
    try:
        proc = start_openocd_server(cmd)
        ready, errors = wait_server_ready(proc, args.telnet_port)

        if not ready:
            error_msg = "; ".join(errors) if errors else "OpenOCD 启动失败或超时"
            result = {
                "status": "error", "action": args.action,
                "error": {"code": "server_failed", "message": error_msg},
            }
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {error_msg}", file=sys.stderr, flush=True)
            sys.exit(1)

        # 连接 Telnet
        telnet = TelnetConnection(port=args.telnet_port)
        telnet.connect()

        # 执行调试命令
        result = execute_action(telnet, args)

        # 成功执行后，写回确认过的参数到工程配置
        if result.get("status") == "ok":
            save_project_config(str(workspace), {
                "board": board or "",
                "interface": interface or "",
                "target": target or "",
                "adapter_speed": adapter_speed or "",
                "transport": transport or "",
            })

        if args.as_json:
            output_json(result)
        else:
            print_result(result, args.action)

    except FileNotFoundError:
        result = {
            "status": "error", "action": args.action,
            "error": {"code": "exe_not_found", "message": f"openocd 不存在或不在 PATH 中: {args.exe}"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr, flush=True)
        sys.exit(1)
    except ConnectionError as e:
        result = {
            "status": "error", "action": args.action,
            "error": {"code": "telnet_connect_failed", "message": str(e)},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr, flush=True)
        sys.exit(1)
    finally:
        if telnet:
            telnet.close()
        if proc:
            cleanup_proc(proc)


def execute_action(telnet: TelnetConnection, args) -> dict:
    """根据 action 执行 Telnet 调试命令"""
    action = args.action
    start_time = time.time()

    if action == "halt":
        telnet.send("halt")
        time.sleep(0.1)
        # halt 的状态信息输出在 stderr 而非 Telnet 响应，需要额外查询
        pc_raw = telnet.send("reg pc")
        xpsr_raw = telnet.send("reg xpsr")
        msp_raw = telnet.send("reg msp")
        pc_regs = parse_reg_single(pc_raw)
        xpsr_regs = parse_reg_single(xpsr_raw)
        msp_regs = parse_reg_single(msp_raw)
        parsed = {"halted": True, **pc_regs, **xpsr_regs, **msp_regs}
        pc = parsed.get("pc", "?")
        return {
            "status": "ok", "action": "halt",
            "summary": f"已暂停，PC={pc}",
            "details": parsed,
        }

    elif action == "resume":
        raw = telnet.send("resume")
        lowered = raw.lower()
        if "not halted" in lowered and "context restore failed" in lowered:
            return {
                "status": "ok", "action": "resume",
                "summary": "目标已在运行",
                "details": {"response": raw, "already_running": True},
            }
        if has_command_error(raw):
            return {
                "status": "error", "action": "resume",
                "error": {"code": "resume_failed", "message": raw or "resume 执行失败"},
            }
        return {
            "status": "ok", "action": "resume",
            "summary": "已恢复运行",
            "details": {"response": raw},
        }

    elif action == "step":
        count = args.count
        steps = []
        for i in range(count):
            telnet.send("step")
            time.sleep(0.15)
            pc_raw = telnet.send("reg pc")
            pc_regs = parse_reg_single(pc_raw)
            pc = pc_regs.get("pc", "?")
            steps.append({"step": i + 1, "pc": pc})
        last_pc = steps[-1]["pc"] if steps else "?"
        return {
            "status": "ok", "action": "step",
            "summary": f"单步{count}次，PC={last_pc}",
            "details": {"steps": steps, "pc": last_pc},
        }

    elif action == "reg":
        # 确保目标已 halt
        telnet.send("halt")
        time.sleep(0.1)
        # OpenOCD 的 reg（列出全部）不显示值，需要逐个查询核心寄存器
        core_reg_names = [
            "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
            "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc",
            "xpsr", "msp", "psp", "primask", "basepri", "faultmask", "control",
        ]
        registers = {}
        for name in core_reg_names:
            raw = telnet.send(f"reg {name}")
            parsed = parse_reg_single(raw)
            registers.update(parsed)
        return {
            "status": "ok", "action": "reg",
            "summary": f"读取到 {len(registers)} 个寄存器",
            "details": {"registers": registers},
        }

    elif action == "read-mem":
        width_cmd = {"8": "mdb", "16": "mdh", "32": "mdw"}
        cmd = f"{width_cmd[args.width]} {args.address} {args.length}"
        raw = telnet.send(cmd)
        memory = parse_mem_response(raw)
        return {
            "status": "ok", "action": "read-mem",
            "summary": f"读取 {args.length} x {args.width}bit @ {args.address}",
            "details": {"width": int(args.width), "memory": memory},
        }

    elif action == "write-mem":
        width_cmd = {"8": "mwb", "16": "mwh", "32": "mww"}
        cmd = f"{width_cmd[args.width]} {args.address} {args.value}"
        raw = telnet.send(cmd)
        # 检查是否有错误
        if has_command_error(raw):
            return {
                "status": "error", "action": "write-mem",
                "error": {"code": "write_failed", "message": raw},
            }
        return {
            "status": "ok", "action": "write-mem",
            "summary": f"已写入 {args.value} @ {args.address} ({args.width}bit)",
            "details": {"address": args.address, "value": args.value, "width": int(args.width)},
        }

    elif action == "bp":
        cmd = f"bp {args.address} {args.bp_length} hw"
        raw = telnet.send(cmd)
        if has_command_error(raw):
            return {
                "status": "error", "action": "bp",
                "error": {"code": "bp_set_failed", "message": f"断点设置失败: {raw}"},
            }
        return {
            "status": "ok", "action": "bp",
            "summary": f"断点已设置 @ {args.address}",
            "details": {"address": args.address, "length": args.bp_length, "type": "hw"},
        }

    elif action == "rbp":
        cmd = f"rbp {args.address}"
        raw = telnet.send(cmd)
        if has_command_error(raw):
            return {
                "status": "error", "action": "rbp",
                "error": {"code": "bp_remove_failed", "message": f"断点移除失败: {raw}"},
            }
        return {
            "status": "ok", "action": "rbp",
            "summary": f"断点已移除 @ {args.address}",
            "details": {"address": args.address},
        }

    elif action == "run-to":
        # 设置断点 -> resume -> 等待 -> halt -> 查 PC -> 移除断点
        telnet.send("halt")
        time.sleep(0.1)
        bp_raw = telnet.send(f"bp {args.address} {args.bp_length} hw")
        if has_command_error(bp_raw):
            return {
                "status": "error", "action": "run-to",
                "error": {"code": "bp_set_failed", "message": f"断点设置失败: {bp_raw}"},
            }

        resume_raw = telnet.send("resume")
        if has_command_error(resume_raw):
            telnet.send(f"rbp {args.address}")
            return {
                "status": "error", "action": "run-to",
                "error": {"code": "resume_failed", "message": f"run-to 恢复失败: {resume_raw}"},
            }
        timeout_s = args.timeout_ms / 1000.0
        time.sleep(timeout_s)

        telnet.send("halt")
        time.sleep(0.1)
        pc_raw = telnet.send("reg pc")
        pc_regs = parse_reg_single(pc_raw)
        pc = pc_regs.get("pc", "")

        # 移除断点
        telnet.send(f"rbp {args.address}")

        # 判断是否命中（比较 PC 和断点地址）
        bp_hit = False
        if pc:
            bp_hit = int(pc, 16) == int(args.address, 16)

        if bp_hit:
            summary = f"断点命中 @ {args.address}，PC={pc}"
        else:
            summary = f"超时未命中断点 @ {args.address}，当前 PC={pc}"

        return {
            "status": "ok", "action": "run-to",
            "summary": summary,
            "details": {
                "bp_address": args.address,
                "bp_hit": bp_hit,
                "timeout_ms": args.timeout_ms,
                "pc": pc,
            },
        }

    return {"status": "error", "action": action, "error": {"code": "unknown_action", "message": f"未知操作: {action}"}}


def print_result(result: dict, action: str):
    """非 JSON 模式下的人类可读输出"""
    if result["status"] == "ok":
        print(f"[{action}] {result.get('summary', '成功')}", flush=True)
        details = result.get("details", {})

        if "registers" in details and action == "reg":
            core_regs = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
                         "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc",
                         "xPSR", "msp", "psp", "primask", "control"]
            regs = details["registers"]
            for name in core_regs:
                if name in regs:
                    print(f"  {name:>10s} = {regs[name]}", flush=True)

        if "memory" in details:
            for m in details["memory"]:
                print(f"  {m['address']}: {m['data']}", flush=True)

        if "steps" in details:
            for s in details["steps"]:
                print(f"  step {s['step']}: PC={s['pc']}", flush=True)

        if "bp_hit" in details:
            hit = "命中" if details["bp_hit"] else "未命中（超时）"
            print(f"  断点: {details.get('bp_address', '?')} - {hit}", flush=True)
    else:
        err = result.get("error", {})
        print(f"[{action}] 失败 - {err.get('message', '未知错误')}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
