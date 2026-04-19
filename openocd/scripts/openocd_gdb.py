"""OpenOCD GDB Server 启动与 one-shot 调试。"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from openocd_gdb_common import build_gdb_commands, parse_gdb_output, run_gdb_commands  # noqa: E402
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
    resolve_artifact_param,
    resolve_project_param,
    resolve_runtime_param,
    resolve_tool_param,
    save_project_config,
    update_state_entry,
    workspace_root,
)


GDB_ACTIONS = [
    "server",
    "run",
    "backtrace",
    "locals",
    "break",
    "continue",
    "next",
    "step",
    "finish",
    "until",
    "frame",
    "print",
    "watch",
    "disassemble",
    "threads",
    "crash-report",
]


def build_openocd_cmd(
    exe: str,
    board: str = "",
    interface: str = "",
    target: str = "",
    search: str = "",
    adapter_speed: str = "",
    transport: str = "",
    gdb_port: int = 3333,
    telnet_port: int = 4444,
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
    cmd.extend(["-c", f"gdb_port {gdb_port}"])
    cmd.extend(["-c", f"telnet_port {telnet_port}"])
    return cmd


def start_openocd_server(cmd: list[str]) -> subprocess.Popen:
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


def wait_server_ready(proc: subprocess.Popen, gdb_port: int, timeout: int = 15) -> tuple[bool, list[str]]:
    started = time.time()
    errors: list[str] = []
    ready = False

    while time.time() - started < timeout:
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
        stripped = line.strip()
        if "Error:" in stripped:
            errors.append(stripped)
        if f"Listening on port {gdb_port}" in stripped or "listening on" in stripped.lower():
            ready = True
            break

    if not ready:
        return False, errors

    critical = [
        "open failed",
        "init mode failed",
        "no device found",
        "cannot connect",
        "error connecting dp",
        "examination failed",
        "failed to read memory",
        "failed to write memory",
        "cannot read idr",
        "polling failed",
    ]
    critical_errors = [item for item in errors if any(keyword in item.lower() for keyword in critical)]
    if critical_errors:
        return False, critical_errors
    return True, errors


def cleanup(proc: subprocess.Popen | None) -> None:
    if proc and proc.poll() is None:
        try:
            if sys.platform == "win32":
                proc.terminate()
            else:
                proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--exe", default=None, help="openocd 路径")
    parser.add_argument("--board", default=None, help="board 配置文件")
    parser.add_argument("--interface", default=None, help="interface 配置文件")
    parser.add_argument("--target", default=None, help="target 配置文件")
    parser.add_argument("--search", default=None, help="额外配置脚本搜索目录")
    parser.add_argument("--adapter-speed", default=None, help="调试速率 kHz")
    parser.add_argument("--transport", default=None, choices=["", "swd", "jtag"], help="传输协议")
    parser.add_argument("--gdb-port", type=int, default=None, help="GDB 端口")
    parser.add_argument("--telnet-port", type=int, default=None, help="Telnet 端口")
    parser.add_argument("--gdb-exe", default=None, help="arm-none-eabi-gdb 路径")
    parser.add_argument("--elf", default=None, help="ELF 文件路径")
    parser.add_argument("--config", default=None, help="skill config.json 路径")
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--json", action="store_true", dest="as_json")


def build_parser(legacy_server: bool) -> argparse.ArgumentParser:
    if legacy_server:
        parser = argparse.ArgumentParser(description="OpenOCD GDB Server 启动")
        add_common_args(parser)
        return parser

    parser = argparse.ArgumentParser(description="OpenOCD GDB Server 启动与调试")
    sub = parser.add_subparsers(dest="command")
    for name in GDB_ACTIONS:
        sub_parser = sub.add_parser(name, help=f"GDB {name}")
        add_common_args(sub_parser)
        if name == "run":
            sub_parser.add_argument("--commands", nargs="+", required=True, help="GDB 命令序列")
        elif name in {"break", "frame", "print", "watch"}:
            sub_parser.add_argument("--expr", required=True, help="表达式或参数")
        elif name in {"until", "disassemble"}:
            sub_parser.add_argument("--expr", default=None, help="表达式或参数")
    return parser


def _legacy_mode() -> bool:
    return not (len(sys.argv) > 1 and sys.argv[1] in GDB_ACTIONS)


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
        "gdb_port": last_debug.get("gdb_port"),
        "telnet_port": last_debug.get("telnet_port"),
        "elf_file": last_build.get("debug_file") or last_build.get("elf_file") or artifacts.get("debug_file"),
        "debug_file": last_build.get("debug_file") or artifacts.get("debug_file"),
    }


def resolve_openocd_params(args, project_config: dict, state_lookup: dict) -> dict:
    """解析 OpenOCD 工程级参数，优先级: CLI > 工程配置 > state.json"""
    board, board_source = resolve_project_param(
        "board",
        args.board,
        project_config=project_config,
        project_keys=["board"],
        state_record=state_lookup,
        state_keys=["board"],
    )
    interface, interface_source = resolve_project_param(
        "interface",
        args.interface,
        project_config=project_config,
        project_keys=["interface"],
        state_record=state_lookup,
        state_keys=["interface"],
    )
    target, target_source = resolve_project_param(
        "target",
        args.target,
        project_config=project_config,
        project_keys=["target"],
        state_record=state_lookup,
        state_keys=["target"],
    )
    adapter_speed, adapter_speed_source = resolve_project_param(
        "adapter_speed",
        args.adapter_speed,
        project_config=project_config,
        project_keys=["adapter_speed"],
        state_record=state_lookup,
        state_keys=["adapter_speed"],
    )
    transport, transport_source = resolve_project_param(
        "transport",
        args.transport,
        project_config=project_config,
        project_keys=["transport"],
        state_record=state_lookup,
        state_keys=["transport"],
    )

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


def _summary(command: str, parsed: dict) -> str:
    if command == "server":
        return "gdb server 已就绪"
    if command == "backtrace" and parsed.get("frames"):
        return f"backtrace 完成，frames={len(parsed['frames'])}"
    if command == "locals" and parsed.get("variables"):
        return f"locals 完成，variables={len(parsed['variables'])}"
    if command == "threads" and parsed.get("threads"):
        return f"threads 完成，threads={len(parsed['threads'])}"
    if command == "print" and parsed.get("value"):
        return f"print 完成，value={parsed['value']}"
    return f"gdb {command} 完成"


def _metrics(parsed: dict) -> dict:
    metrics: dict[str, int] = {}
    if parsed.get("frames"):
        metrics["frames"] = len(parsed["frames"])
    if parsed.get("variables"):
        metrics["variables"] = len(parsed["variables"])
    if parsed.get("registers"):
        metrics["registers"] = len(parsed["registers"])
    if parsed.get("threads"):
        metrics["threads"] = len(parsed["threads"])
    if parsed.get("disassembly"):
        metrics["instructions"] = len(parsed["disassembly"])
    return metrics


def main() -> None:
    legacy_server = _legacy_mode()
    parser = build_parser(legacy_server)
    args = parser.parse_args()
    if legacy_server:
        args.command = "server"

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
        exe, parameter_sources["exe"] = resolve_tool_param(
            "exe",
            args.exe,
            local_config=config,
            local_keys=["exe"],
            path_candidates=["openocd.exe", "openocd"],
            default="openocd",
            required=True,
        )

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

        search, parameter_sources["search"] = resolve_runtime_param(
            "search",
            args.search,
            local_config=config,
            local_keys=["scripts_dir"],
            state_record=state_lookup,
            state_keys=["search"],
        )
        gdb_port, parameter_sources["gdb_port"] = resolve_runtime_param(
            "gdb_port",
            args.gdb_port,
            local_config=config,
            local_keys=["gdb_port"],
            state_record=state_lookup,
            state_keys=["gdb_port"],
        )
        telnet_port, parameter_sources["telnet_port"] = resolve_runtime_param(
            "telnet_port",
            args.telnet_port,
            local_config=config,
            local_keys=["telnet_port"],
            state_record=state_lookup,
            state_keys=["telnet_port"],
        )
    except ValueError as exc:
        result = make_result(
            status="error",
            action=getattr(args, "command", "server"),
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

    if not board and not interface and not target:
        message = "必须提供 --board 或 --interface + --target"
        result = make_result(
            status="error",
            action=args.command,
            summary=message,
            details={},
            context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "missing_config", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)

    gdb_exe = None
    elf_file = None
    if args.command != "server":
        try:
            gdb_exe, parameter_sources["gdb_exe"] = resolve_tool_param(
                "gdb_exe",
                args.gdb_exe,
                local_config=config,
                local_keys=["gdb_exe"],
                path_candidates=["arm-none-eabi-gdb.exe", "arm-none-eabi-gdb"],
                default="arm-none-eabi-gdb",
            )
            elf_file, parameter_sources["elf"] = resolve_artifact_param(
                "elf",
                args.elf,
                project_config=project_config,
                project_keys=["elf", "default_elf"],
                state_record=state_lookup,
                state_keys=["elf_file", "debug_file"],
                normalize_as_path=True,
            )
        except ValueError as exc:
            result = make_result(
                status="error",
                action=args.command,
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

        if not os.path.isfile(gdb_exe):
            message = f"arm-none-eabi-gdb 不存在: {gdb_exe}"
            result = make_result(
                status="error",
                action=args.command,
                summary=message,
                details={},
                context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
                error={"code": "gdb_not_found", "message": message},
                timing=make_timing(started_at, (time.time() - started_ts) * 1000),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {message}", file=sys.stderr)
            sys.exit(1)

    cmd = build_openocd_cmd(
        exe=exe,
        board=board or "",
        interface=interface or "",
        target=target or "",
        search=search or "",
        adapter_speed=str(adapter_speed or ""),
        transport=transport or "",
        gdb_port=int(gdb_port or 3333),
        telnet_port=int(telnet_port or 4444),
    )

    proc: subprocess.Popen | None = None
    try:
        proc = start_openocd_server(cmd)
        ready, errors = wait_server_ready(proc, int(gdb_port or 3333))
        if not ready:
            message = "; ".join(errors) if errors else "GDB Server 启动失败或超时"
            result = make_result(
                status="error",
                action=args.command,
                summary="GDB Server 启动失败",
                details={"errors": errors},
                context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
                error={"code": "gdbserver_failed", "message": message},
                timing=make_timing(started_at, (time.time() - started_ts) * 1000),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"[{args.command}] 失败 — {message}", file=sys.stderr)
            sys.exit(1)

        if args.command == "server":
            state_info = update_state_entry(
                "last_debug",
                {
                    "provider": "openocd",
                    "action": "server",
                    "board": board or "",
                    "interface": interface or "",
                    "target": target or "",
                    "search": search or "",
                    "adapter_speed": adapter_speed or "",
                    "transport": transport or "",
                    "gdb_port": int(gdb_port or 3333),
                    "telnet_port": int(telnet_port or 4444),
                },
                str(workspace),
            )
            result = make_result(
                status="ok",
                action="server",
                summary=_summary("server", {}),
                details={
                    "gdb_port": int(gdb_port or 3333),
                    "telnet_port": int(telnet_port or 4444),
                    "pid": proc.pid,
                    "connect_cmd": f"arm-none-eabi-gdb -ex 'target remote localhost:{int(gdb_port or 3333)}'",
                    "warnings": errors,
                },
                context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
                state=state_info,
                timing=make_timing(started_at, (time.time() - started_ts) * 1000),
            )
            if args.as_json:
                output_json(result)
            else:
                print("[gdb-server] GDB Server 已就绪")
                print(f"  GDB 端口: {int(gdb_port or 3333)}")
                print(f"  Telnet 端口: {int(telnet_port or 4444)}")
                print(f"  PID: {proc.pid}")
            try:
                proc.wait()
            except KeyboardInterrupt:
                pass
            return

        try:
            if args.command == "run":
                gdb_commands = list(args.commands)
            else:
                gdb_commands = build_gdb_commands(args.command, getattr(args, "expr", None))
        except ValueError as exc:
            result = make_result(
                status="error",
                action=args.command,
                summary=str(exc),
                details={},
                context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
                error={"code": "invalid_args", "message": str(exc)},
                timing=make_timing(started_at, (time.time() - started_ts) * 1000),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {exc}", file=sys.stderr)
            sys.exit(1)

        gdb_result = run_gdb_commands(gdb_exe, elf_file or "", f"localhost:{int(gdb_port or 3333)}", gdb_commands)
        elapsed_ms = (time.time() - started_ts) * 1000
        if gdb_result["status"] == "error":
            result = make_result(
                status="error",
                action=args.command,
                summary="GDB 执行失败",
                details={"gdb_port": int(gdb_port or 3333), "errors": errors},
                context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
                artifacts=build_artifacts(debug_file=elf_file),
                error={"code": "gdb_error", "message": gdb_result.get("error", gdb_result.get("stderr", "GDB 执行失败"))},
                timing=make_timing(started_at, elapsed_ms),
            )
        else:
            parsed = parse_gdb_output(gdb_result["stdout"], args.command)
            artifacts = build_artifacts(debug_file=elf_file)
            state_info = update_state_entry(
                "last_debug",
                {
                    "provider": "openocd",
                    "action": args.command,
                    "board": board or "",
                    "interface": interface or "",
                    "target": target or "",
                    "search": search or "",
                    "adapter_speed": adapter_speed or "",
                    "transport": transport or "",
                    "gdb_port": int(gdb_port or 3333),
                    "telnet_port": int(telnet_port or 4444),
                    "debug_file": elf_file or "",
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
                action=args.command,
                summary=_summary(args.command, parsed),
                details={
                    "gdb_port": int(gdb_port or 3333),
                    "telnet_port": int(telnet_port or 4444),
                    "commands": gdb_commands,
                    "output": parsed.get("output", ""),
                    "returncode": gdb_result.get("returncode", 0),
                    "warnings": errors,
                    **{key: value for key, value in parsed.items() if key != "output"},
                },
                context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
                artifacts=artifacts,
                metrics=_metrics(parsed),
                state=state_info,
                next_actions=["可继续基于 last_debug 复用 cfg 组合和 debug_file"],
                timing=make_timing(started_at, elapsed_ms),
            )

        if args.as_json:
            output_json(result)
        elif result["status"] == "ok":
            print(f"[gdb-{args.command}] {result['summary']}")
            output = result.get("details", {}).get("output", "")
            if output:
                print(output)
        else:
            print(f"[gdb-{args.command}] 失败 — {result['error']['message']}", file=sys.stderr)
            sys.exit(1)
    except FileNotFoundError:
        message = f"openocd 不存在或不在 PATH 中: {exe}"
        result = make_result(
            status="error",
            action=args.command,
            summary=message,
            details={},
            context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "exe_not_found", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)
    finally:
        if args.command != "server":
            cleanup(proc)


if __name__ == "__main__":
    main()
