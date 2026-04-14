"""OpenOCD 探针探测、固件烧录、Flash 擦除、目标复位与底层查询。"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from openocd_runtime import (  # noqa: E402
    build_artifacts,
    default_config_path,
    get_state_entry,
    hidden_subprocess_kwargs,
    is_missing,
    load_json_file,
    load_local_config,
    load_project_config,
    load_workspace_state,
    make_result,
    make_timing,
    normalize_path,
    now_iso,
    output_json,
    parameter_context,
    resolve_param,
    save_project_config,
    update_state_entry,
    workspace_root,
)


ERROR_PATTERNS = [
    (r"Error:\s*open failed", "adapter_open_failed", "调试器打开失败，请检查 USB 连接和驱动"),
    (r"Error:\s*Failed to open device", "adapter_open_failed", "调试器打开失败，请检查 USB 连接和驱动"),
    (r"Error:\s*No.+device found", "no_device", "未找到调试器设备，请检查 USB 连接和驱动"),
    (r"Error:\s*unable to find.+cfg", "cfg_not_found", "未找到指定的配置文件，请确认 cfg 路径"),
    (r"Error:\s*Transport .+ is not selected", "transport_error", "传输协议未选择，请检查 transport 设置"),
    (r"Error:\s*init mode failed", "init_failed", "初始化失败，请检查连线、供电和配置组合"),
    (r"Error:\s*Could not verify flash", "verify_failed", "固件校验失败，Flash 可能损坏或写保护"),
    (r"Error:\s*flash write failed", "flash_write_failed", "Flash 写入失败，请检查固件文件和目标状态"),
    (r"Error:\s*timed out while waiting for target halted", "target_timeout", "等待目标暂停超时，请检查连接"),
    (r"Error:\s*Target not halted", "target_not_halted", "目标未暂停，擦除或写入前需要先 halt 目标"),
    (r"Error:\s*couldn't bind .+ to socket", "port_busy", "端口被占用，请检查是否有其他 OpenOCD 实例运行"),
    (r"Error:\s*Target not examined yet", "target_not_examined", "目标未初始化，请检查 target 配置"),
    (r"Error:\s*flash bank", "flash_bank_error", "Flash bank 配置错误，请确认 target 配置匹配芯片"),
    (r"Error:\s*device is read protected", "read_protected", "芯片读保护已开启，需要先解锁（本工具不自动解锁）"),
    (r"failed erasing sectors", "erase_failed", "Flash 扇区擦除失败，请检查目标是否 halt、读写保护状态及 flash bank 配置"),
    (r"mass erase failed", "mass_erase_failed", "整片擦除失败，请检查目标是否 halt、读写保护状态及 target 配置"),
    (r"Error:\s*Cannot connect", "cannot_connect", "无法连接目标，请检查连线、供电和接口类型"),
    (r"Error:\s*Could not connect", "cannot_connect", "无法连接目标，请检查连线、供电和接口类型"),
]

ALL_ACTIONS = ["probe", "flash", "erase", "reset", "reset-init", "targets", "flash-banks", "adapter-info", "raw"]


def build_openocd_cmd(
    exe: str,
    board: str = "",
    interface: str = "",
    target: str = "",
    search: str = "",
    adapter_speed: str = "",
    transport: str = "",
    extra_commands: list[str] | None = None,
) -> list[str]:
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
    for command in extra_commands or []:
        cmd.extend(["-c", command])
    return cmd


def infer_mass_erase_command(target: str, board: str) -> str:
    cfg = (target or board).lower()
    command_map = {
        "stm32f0": "stm32f1x mass_erase 0",
        "stm32f1": "stm32f1x mass_erase 0",
        "stm32f2": "stm32f2x mass_erase 0",
        "stm32f3": "stm32f1x mass_erase 0",
        "stm32f4": "stm32f4x mass_erase 0",
        "stm32f7": "stm32f2x mass_erase 0",
        "stm32g0": "stm32l4x mass_erase 0",
        "stm32g4": "stm32l4x mass_erase 0",
        "stm32h7": "stm32h7x mass_erase 0",
        "stm32l0": "stm32l0x mass_erase 0",
        "stm32l1": "stm32lx mass_erase 0",
        "stm32l4": "stm32l4x mass_erase 0",
        "stm32u5": "stm32l4x mass_erase 0",
        "gd32f1": "stm32f1x mass_erase 0",
        "gd32f4": "stm32f4x mass_erase 0",
    }
    for key, command in command_map.items():
        if key in cfg:
            return command
    return ""


def build_action_commands(
    action: str,
    *,
    board: str = "",
    target: str = "",
    file: str = "",
    address: str = "",
    reset_mode: str = "run",
    bank: str = "",
    erase_mode: str = "auto",
    raw_commands: list[str] | None = None,
) -> tuple[list[str], str | None]:
    if action == "probe":
        return ["init", "targets", "shutdown"], None
    if action == "targets":
        return ["init", "targets", "shutdown"], None
    if action == "flash-banks":
        return ["init", "flash banks", "shutdown"], None
    if action == "adapter-info":
        return ["adapter name", "transport list", "adapter speed", "shutdown"], None
    if action == "flash":
        file_abs = os.path.abspath(file).replace("\\", "/")
        if file.lower().endswith(".bin"):
            return ["init", f"program {{{file_abs}}} verify reset exit {address}"], None
        return ["init", f"program {{{file_abs}}} verify reset exit"], None
    if action == "erase":
        bank_idx = bank if bank else "0"
        mass_erase_cmd = infer_mass_erase_command(target, board)
        commands = ["init", "reset halt"]
        if erase_mode == "mass":
            commands.append(mass_erase_cmd)
        elif erase_mode == "sector":
            commands.append(f"flash erase_sector {bank_idx} 0 last")
        else:
            commands.append(mass_erase_cmd or f"flash erase_sector {bank_idx} 0 last")
        commands.append("shutdown")
        return commands, None
    if action in ("reset", "reset-init"):
        if action == "reset-init" or reset_mode == "init":
            return ["init", "reset init", "shutdown"], None
        if reset_mode == "halt":
            return ["init", "reset halt", "shutdown"], None
        return ["init", "reset run", "shutdown"], None
    if action == "raw":
        return (raw_commands or []) + ["shutdown"], None
    return [], "unknown_action"


def parse_output(combined: str, action: str) -> dict:
    result = {"raw": combined}
    for pattern, code, message in ERROR_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return {"error_code": code, "error_message": message, "raw": combined}

    if action in ("probe", "targets"):
        target_match = re.search(r"Info\s*:\s*(\S+\.cm\S*)\s", combined)
        if target_match:
            result["core"] = target_match.group(1)
        tap_match = re.search(r"Info\s*:\s*JTAG tap:\s*(\S+)", combined)
        if tap_match:
            result["jtag_tap"] = tap_match.group(1)
        result["targets"] = [line.strip() for line in combined.splitlines() if line.strip() and "tap/device found" not in line.lower()]

    elif action == "flash":
        speed_match = re.search(r"wrote\s+(\d+)\s+bytes.*?in\s+([\d.]+)s\s+\(([\d.]+)\s+KiB/s\)", combined)
        if speed_match:
            result["bytes_written"] = int(speed_match.group(1))
            result["elapsed_s"] = float(speed_match.group(2))
            result["speed_kbps"] = float(speed_match.group(3))
        if "verified OK" in combined or "** Verified OK **" in combined:
            result["verified"] = True
        if "** Programming Finished **" in combined:
            result["programmed"] = True

    elif action == "erase":
        if "erased sectors" in combined.lower():
            result["erased"] = True
            result["mode"] = "sector"
            sector_match = re.search(
                r"erased sectors\s+(\d+)\s+through\s+(\d+)\s+on flash bank\s+(\d+)\s+in\s+([\d.]+)s",
                combined,
                re.IGNORECASE,
            )
            if sector_match:
                result["first_sector"] = int(sector_match.group(1))
                result["last_sector"] = int(sector_match.group(2))
                result["bank"] = int(sector_match.group(3))
                result["elapsed_s"] = float(sector_match.group(4))
        elif "mass erase complete" in combined.lower():
            result["erased"] = True
            result["mode"] = "mass"

    elif action == "flash-banks":
        result["flash_banks"] = [line.strip() for line in combined.splitlines() if "flash bank" in line.lower()]

    elif action == "adapter-info":
        name_match = re.search(r"adapter name:\s*(.+)", combined, re.IGNORECASE)
        if name_match:
            result["adapter_name"] = name_match.group(1).strip()
        transport_match = re.search(r"Transport\s+\w+\s+available", combined)
        if transport_match:
            result["transport_info"] = transport_match.group(0)

    return result


def run_openocd(
    exe: str,
    action: str,
    board: str = "",
    interface: str = "",
    target: str = "",
    search: str = "",
    adapter_speed: str = "",
    transport: str = "",
    file: str = "",
    address: str = "",
    reset_mode: str = "run",
    bank: str = "",
    erase_mode: str = "auto",
    raw_commands: list[str] | None = None,
) -> dict:
    if not board and not interface and not target:
        return {"status": "error", "action": action, "error": {"code": "missing_config", "message": "必须提供 --board 或 --interface + --target"}}

    if action == "flash":
        if not file:
            return {"status": "error", "action": action, "error": {"code": "missing_file", "message": "flash 必须提供 --file 固件文件路径"}}
        if file.lower().endswith(".bin") and not address:
            return {"status": "error", "action": action, "error": {"code": "missing_address", "message": ".bin 文件必须提供 --address 烧录地址"}}

    action_commands, error_code = build_action_commands(
        action,
        board=board,
        target=target,
        file=file,
        address=address,
        reset_mode=reset_mode,
        bank=bank,
        erase_mode=erase_mode,
        raw_commands=raw_commands,
    )
    if error_code:
        return {"status": "error", "action": action, "error": {"code": error_code, "message": f"未知动作: {action}"}}
    if action == "erase" and not any("flash erase_sector" in item or "mass_erase" in item for item in action_commands):
        return {"status": "error", "action": action, "error": {"code": "mass_erase_unsupported", "message": "当前 target/board 未配置 mass erase 命令，请改用 --mode sector 或补充映射"}}

    started = time.time()
    try:
        proc = subprocess.run(
            build_openocd_cmd(
                exe=exe,
                board=board,
                interface=interface,
                target=target,
                search=search,
                adapter_speed=adapter_speed,
                transport=transport,
                extra_commands=action_commands,
            ),
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )
    except FileNotFoundError:
        return {"status": "error", "action": action, "error": {"code": "exe_not_found", "message": f"openocd 不存在或不在 PATH 中: {exe}"}}
    except subprocess.TimeoutExpired:
        return {"status": "error", "action": action, "error": {"code": "timeout", "message": "OpenOCD 执行超时(120s)"}}
    except Exception as exc:  # pragma: no cover
        return {"status": "error", "action": action, "error": {"code": "exec_error", "message": str(exc)}}

    elapsed_ms = int((time.time() - started) * 1000)
    combined = proc.stderr + "\n" + proc.stdout
    parsed = parse_output(combined, action)
    if "error_code" in parsed:
        return {
            "status": "error",
            "action": action,
            "error": {"code": parsed["error_code"], "message": parsed["error_message"]},
            "details": {"board": board, "interface": interface, "target": target, "elapsed_ms": elapsed_ms, "returncode": proc.returncode},
        }

    details = {"board": board, "interface": interface, "target": target, "elapsed_ms": elapsed_ms, "returncode": proc.returncode}
    details.update({key: value for key, value in parsed.items() if key != "raw"})
    summary = f"{action} 成功"
    if action == "flash" and parsed.get("speed_kbps"):
        summary = f"flash 成功，{parsed['bytes_written']} bytes @ {parsed['speed_kbps']} KiB/s"
    elif action == "erase" and parsed.get("mode") == "mass":
        summary = "erase 成功，整片擦除完成"
    elif action == "erase" and parsed.get("mode") == "sector":
        summary = f"erase 成功，sector {parsed.get('first_sector', 0)}-{parsed.get('last_sector', 'last')}"

    status = "ok"
    if proc.returncode != 0:
        if action == "flash" and parsed.get("verified"):
            status = "ok"
        elif action in ("probe", "targets") and (parsed.get("jtag_tap") or parsed.get("core")):
            status = "ok"
        else:
            status = "error"
            error_lines = re.findall(r"Error:\s*(.+)", combined)
            return {
                "status": "error",
                "action": action,
                "error": {"code": "command_failed", "message": error_lines[-1].strip() if error_lines else f"执行返回非零退出码: {proc.returncode}"},
                "details": details,
            }

    return {"status": status, "action": action, "summary": summary, "details": details}


def _state_lookup(state: dict) -> dict:
    last_build = get_state_entry(state, "last_build")
    last_flash = get_state_entry(state, "last_flash")
    last_debug = get_state_entry(state, "last_debug")
    artifacts = last_build.get("artifacts", {})
    return {
        "board": last_debug.get("board") or last_flash.get("board"),
        "interface": last_debug.get("interface") or last_flash.get("interface"),
        "target": last_debug.get("target") or last_flash.get("target"),
        "search": last_debug.get("search"),
        "adapter_speed": last_debug.get("adapter_speed") or last_flash.get("adapter_speed"),
        "transport": last_debug.get("transport") or last_flash.get("transport"),
        "flash_file": last_build.get("flash_file") or artifacts.get("flash_file"),
    }


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


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenOCD 探针探测/固件烧录/擦除/复位")
    parser.add_argument("action", choices=ALL_ACTIONS)
    parser.add_argument("--exe", default=None, help="openocd 路径")
    parser.add_argument("--board", default=None, help="board 配置文件")
    parser.add_argument("--interface", default=None, help="interface 配置文件")
    parser.add_argument("--target", default=None, help="target 配置文件")
    parser.add_argument("--search", default=None, help="额外配置脚本搜索目录")
    parser.add_argument("--adapter-speed", default=None, help="调试速率 kHz")
    parser.add_argument("--transport", default=None, choices=["", "swd", "jtag"], help="传输协议")
    parser.add_argument("--file", default=None, help="固件文件路径（flash 用）")
    parser.add_argument("--address", default=None, help="烧录地址（flash .bin 用）")
    parser.add_argument("--mode", default="run", choices=["halt", "run", "init", "auto", "mass", "sector"], help="reset/erase 模式")
    parser.add_argument("--bank", default=None, help="Flash bank 编号（erase 用，默认 0）")
    parser.add_argument("--command", nargs="+", default=None, help="raw 模式下执行的 OpenOCD 命令列表")
    parser.add_argument("--config", default=None, help="skill config.json 路径")
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    started_at = now_iso()
    started_ts = time.time()
    workspace = workspace_root(args.workspace)
    config_path = normalize_path(args.config or str(default_config_path(__file__)))
    config = load_json_file(config_path)
    state = load_workspace_state(str(workspace))
    state_lookup = _state_lookup(state)
    project_config = load_project_config(str(workspace))

    # 解析 OpenOCD 工程级参数
    oc_params = resolve_openocd_params(args, project_config, state_lookup)

    parameter_sources: dict[str, str] = {}
    try:
        exe, parameter_sources["exe"] = resolve_param("exe", args.exe, config=config, config_keys=["exe"], required=True)

        # 从工程配置或 state 解析 board/interface/target
        board = oc_params["board"]
        parameter_sources["board"] = oc_params["board_source"]
        interface = oc_params["interface"]
        parameter_sources["interface"] = oc_params["interface_source"]
        target = oc_params["target"]
        parameter_sources["target"] = oc_params["target_source"]
        adapter_speed = oc_params["adapter_speed"]
        parameter_sources["adapter_speed"] = oc_params["adapter_speed_source"]
        transport = oc_params["transport"]
        parameter_sources["transport"] = oc_params["transport_source"]

        search, parameter_sources["search"] = resolve_param("search", args.search, config=config, config_keys=["scripts_dir"], state_record=state_lookup, state_keys=["search"])
        file_path, parameter_sources["file"] = resolve_param("file", args.file, config=config, config_keys=["default_file"], state_record=state_lookup, state_keys=["flash_file"], normalize_as_path=True)
    except ValueError as exc:
        result = make_result(
            status="error",
            action=args.action,
            summary=str(exc),
            details={},
            context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "missing_param", "message": str(exc)},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.action == "raw" and int(config.get("operation_mode", 1)) >= 3:
        message = "operation_mode=3 时禁止直接执行 raw 命令，请先切换模式或显式确认后再执行"
        result = make_result(
            status="error",
            action="raw",
            summary=message,
            details={},
            context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "confirmation_required", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)

    if args.action == "flash" and file_path and not os.path.isfile(file_path):
        message = f"固件文件不存在: {file_path}"
        result = make_result(
            status="error",
            action="flash",
            summary=message,
            details={},
            context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "file_not_found", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)

    raw_result = run_openocd(
        exe=exe,
        action=args.action,
        board=board or "",
        interface=interface or "",
        target=target or "",
        search=search or "",
        adapter_speed=str(adapter_speed or ""),
        transport=transport or "",
        file=file_path or "",
        address=args.address or "",
        reset_mode=args.mode if args.action == "reset" else "run",
        bank=args.bank or "",
        erase_mode=args.mode if args.action == "erase" else "auto",
        raw_commands=args.command,
    )
    elapsed_ms = (time.time() - started_ts) * 1000

    if raw_result["status"] == "error":
        result = make_result(
            status="error",
            action=args.action,
            summary=raw_result["error"]["message"],
            details=raw_result.get("details", {}),
            context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error=raw_result["error"],
            timing=make_timing(started_at, elapsed_ms),
        )
    else:
        details = raw_result.get("details", {})
        artifacts = build_artifacts(flash_file=file_path, input_file=file_path)
        metrics = {}
        if "bytes_written" in details:
            metrics["bytes_written"] = details["bytes_written"]
        if "speed_kbps" in details:
            metrics["speed_kbps"] = details["speed_kbps"]
        state_info = None
        if args.action == "flash":
            state_info = update_state_entry(
                "last_flash",
                {
                    "provider": "openocd",
                    "action": "flash",
                    "board": board or "",
                    "interface": interface or "",
                    "target": target or "",
                    "search": search or "",
                    "adapter_speed": adapter_speed or "",
                    "transport": transport or "",
                    "flash_file": file_path or "",
                    "artifacts": artifacts,
                },
                str(workspace),
            )
            # 写回确认过的参数到工程配置
            save_project_config(str(workspace), {
                "board": board or "",
                "interface": interface or "",
                "target": target or "",
                "adapter_speed": adapter_speed or "",
                "transport": transport or "",
            })
        result = make_result(
            status="ok",
            action=args.action,
            summary=raw_result["summary"],
            details=details,
            context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            artifacts=artifacts,
            metrics=metrics,
            state=state_info,
            next_actions=["可继续复用 last_flash/last_debug 串联后续流程"] if args.action == "flash" else None,
            timing=make_timing(started_at, elapsed_ms),
        )

    if args.as_json:
        output_json(result)
        return

    if result["status"] == "ok":
        print(f"[{args.action}] {result['summary']}")
    else:
        print(f"[{args.action}] 失败 — {result['error']['message']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
