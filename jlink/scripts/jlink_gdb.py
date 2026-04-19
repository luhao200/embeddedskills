"""J-Link one-shot GDB 源码级调试。"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from jlink_gdb_common import build_gdb_commands, parse_gdb_output, run_gdb_commands  # noqa: E402
from jlink_runtime import (  # noqa: E402
    resolve_artifact_param,
    resolve_project_param,
    resolve_runtime_param,
    resolve_tool_param,
    build_artifacts,
    default_config_path,
    get_state_entry,
    hidden_subprocess_kwargs,
    is_missing,
    load_json_file,
    load_project_config,
    load_workspace_state,
    make_result,
    make_timing,
    normalize_path,
    now_iso,
    output_json,
    parameter_context,
    save_project_config,
    update_state_entry,
    workspace_root,
)


ALL_COMMANDS = [
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


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def start_gdbserver(
    gdbserver_exe: str,
    device: str,
    interface: str = "SWD",
    speed: str = "4000",
    serial_no: str = "",
    gdb_port: int = 0,
) -> tuple[subprocess.Popen, int]:
    if not gdb_port:
        gdb_port = find_free_port()

    cmd = [
        gdbserver_exe,
        "-device",
        device,
        "-if",
        interface,
        "-speed",
        speed,
        "-port",
        str(gdb_port),
        "-noir",
        "-LocalhostOnly",
        "-nologtofile",
        "-singlerun",
    ]
    if serial_no:
        cmd.extend(["-select", f"USB={serial_no}"])

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **hidden_subprocess_kwargs(new_process_group=True),
    )
    return proc, gdb_port


def wait_gdbserver_ready(proc: subprocess.Popen, timeout: int = 15) -> tuple[bool, str]:
    started = time.time()
    captured: list[str] = []
    while time.time() - started < timeout:
        if proc.poll() is not None:
            captured.append(proc.stderr.read())
            return False, "\n".join(line for line in captured if line).strip()
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.1)
            continue
        captured.append(line.strip())
        if "Waiting for GDB connection" in line or "Connected to target" in line:
            return True, "\n".join(captured)
        if "Cannot connect" in line or "Could not connect" in line:
            return False, "\n".join(captured)
    return False, "\n".join(captured)


def cleanup(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
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
    parser.add_argument("--gdbserver-exe", default=None, help="JLinkGDBServerCL.exe 路径")
    parser.add_argument("--gdb-exe", default=None, help="arm-none-eabi-gdb 路径")
    parser.add_argument("--device", default=None, help="芯片型号")
    parser.add_argument("--elf", default=None, help="ELF 文件路径")
    parser.add_argument("--interface", default=None, help="调试接口")
    parser.add_argument("--speed", default=None, help="调试速率 kHz")
    parser.add_argument("--serial-no", default=None, help="探针序列号")
    parser.add_argument("--gdb-port", type=int, default=0, help="GDB 端口，0=自动")
    parser.add_argument("--config", default=None, help="skill config.json 路径")
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--json", action="store_true", dest="as_json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="J-Link GDB Server 调试")
    sub = parser.add_subparsers(dest="command")
    for name in ALL_COMMANDS:
        sub_parser = sub.add_parser(name, help=f"GDB {name}")
        add_common_args(sub_parser)
        if name == "run":
            sub_parser.add_argument("--commands", nargs="+", required=True, help="GDB 命令序列")
        elif name in {"break", "frame", "print", "watch"}:
            sub_parser.add_argument("--expr", required=True, help="表达式或参数")
        elif name in {"until", "disassemble"}:
            sub_parser.add_argument("--expr", default=None, help="表达式或参数")
    return parser


def _state_lookup(state: dict) -> dict:
    last_build = get_state_entry(state, "last_build")
    last_flash = get_state_entry(state, "last_flash")
    last_debug = get_state_entry(state, "last_debug")
    artifacts = last_build.get("artifacts", {})
    return {
        "device": last_debug.get("device") or last_flash.get("device"),
        "elf_file": last_build.get("debug_file") or last_build.get("elf_file") or artifacts.get("debug_file"),
        "debug_file": last_build.get("debug_file") or artifacts.get("debug_file"),
        "serial_no": last_debug.get("serial_no") or last_flash.get("serial_no"),
        "interface": last_debug.get("interface") or last_flash.get("interface"),
        "speed": last_debug.get("speed") or last_flash.get("speed"),
    }


def resolve_device_params(args, project_config: dict, state_lookup: dict) -> dict:
    """解析 device/interface/speed 参数，优先级: CLI > 工程配置 > state.json > default"""
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

    return {
        "device": device,
        "device_source": device_source,
        "interface": interface,
        "interface_source": interface_source,
        "speed": speed,
        "speed_source": speed_source,
    }


def _summary(command: str, parsed: dict) -> str:
    if command == "continue" and parsed.get("timed_out"):
        return "continue 已执行，目标在超时窗口内未停下"
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
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    started_at = now_iso()
    started_ts = time.time()
    workspace = workspace_root(args.workspace)
    config_path = normalize_path(args.config or str(default_config_path(__file__)))
    config = load_json_file(config_path)
    state = load_workspace_state(str(workspace))
    state_lookup = _state_lookup(state)
    project_config = load_project_config(str(workspace))

    # 解析 device/interface/speed 参数
    dev_params = resolve_device_params(args, project_config, state_lookup)

    parameter_sources: dict[str, str] = {}
    try:
        gdbserver_exe, parameter_sources["gdbserver_exe"] = resolve_tool_param(
            "gdbserver_exe",
            args.gdbserver_exe,
            local_config=config,
            local_keys=["gdbserver_exe"],
            path_candidates=["JLinkGDBServerCL.exe"],
            default="JLinkGDBServerCL.exe",
            required=True,
        )
        gdb_exe, parameter_sources["gdb_exe"] = resolve_tool_param(
            "gdb_exe",
            args.gdb_exe,
            local_config=config,
            local_keys=["gdb_exe"],
            path_candidates=["arm-none-eabi-gdb.exe", "arm-none-eabi-gdb"],
            default="arm-none-eabi-gdb",
            required=True,
        )
        # device 从工程配置或 state 解析
        device = dev_params["device"]
        parameter_sources["device"] = dev_params["device_source"]
        if is_missing(device):
            raise ValueError("缺少必要参数: device")

        elf_file, parameter_sources["elf"] = resolve_artifact_param(
            "elf",
            args.elf,
            project_config=project_config,
            project_keys=["elf", "default_elf"],
            state_record=state_lookup,
            state_keys=["elf_file", "debug_file"],
            normalize_as_path=True,
            workspace=str(workspace),
        )
        interface = dev_params["interface"]
        parameter_sources["interface"] = dev_params["interface_source"]
        speed = dev_params["speed"]
        parameter_sources["speed"] = dev_params["speed_source"]
        serial_no, parameter_sources["serial_no"] = resolve_runtime_param(
            "serial_no",
            args.serial_no,
            local_config=config,
            local_keys=["serial_no"],
            state_record=state_lookup,
            state_keys=["serial_no"],
        )
    except ValueError as exc:
        result = make_result(
            status="error",
            action=args.command,
            summary=str(exc),
            details={},
            context=parameter_context(
                provider="jlink",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
                config_path=config_path,
            ),
            error={"code": "missing_param", "message": str(exc)},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(gdbserver_exe):
        message = f"JLinkGDBServerCL.exe 不存在: {gdbserver_exe}"
        result = make_result(
            status="error",
            action=args.command,
            summary=message,
            details={},
            context=parameter_context(provider="jlink", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "gdbserver_not_found", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(gdb_exe):
        message = f"arm-none-eabi-gdb 不存在: {gdb_exe}"
        result = make_result(
            status="error",
            action=args.command,
            summary=message,
            details={},
            context=parameter_context(provider="jlink", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "gdb_not_found", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)

    procs: list[subprocess.Popen] = []
    try:
        gdb_proc, gdb_port = start_gdbserver(
            gdbserver_exe=gdbserver_exe,
            device=device,
            interface=interface or "SWD",
            speed=speed or "4000",
            serial_no=serial_no or "",
            gdb_port=args.gdb_port,
        )
        procs.append(gdb_proc)

        ready, server_output = wait_gdbserver_ready(gdb_proc)
        if not ready:
            result = make_result(
                status="error",
                action=args.command,
                summary="GDB Server 启动失败",
                details={"device": device, "server_output": server_output},
                context=parameter_context(
                    provider="jlink",
                    workspace=str(workspace),
                    parameter_sources=parameter_sources,
                    config_path=config_path,
                ),
                error={"code": "gdbserver_failed", "message": server_output or "GDB Server 启动失败"},
                timing=make_timing(started_at, (time.time() - started_ts) * 1000),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"[gdb-{args.command}] 失败 — {result['error']['message']}", file=sys.stderr)
            sys.exit(1)

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
                context=parameter_context(
                    provider="jlink",
                    workspace=str(workspace),
                    parameter_sources=parameter_sources,
                    config_path=config_path,
                ),
                error={"code": "invalid_args", "message": str(exc)},
                timing=make_timing(started_at, (time.time() - started_ts) * 1000),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {exc}", file=sys.stderr)
            sys.exit(1)

        gdb_result = run_gdb_commands(gdb_exe, elf_file or "", f"localhost:{gdb_port}", gdb_commands)
        elapsed_ms = (time.time() - started_ts) * 1000
        if gdb_result["status"] == "timeout" and args.command == "continue":
            parsed = parse_gdb_output(gdb_result.get("stdout", ""), args.command)
            parsed["timed_out"] = True
            artifacts = build_artifacts(debug_file=elf_file)
            state_info = update_state_entry(
                "last_debug",
                {
                    "provider": "jlink",
                    "action": args.command,
                    "device": device,
                    "interface": interface or "SWD",
                    "speed": speed or "4000",
                    "serial_no": serial_no or "",
                    "debug_file": elf_file or "",
                    "artifacts": artifacts,
                },
                str(workspace),
            )
            save_project_config(str(workspace), {
                "device": device,
                "interface": interface or "SWD",
                "speed": speed or "4000",
            })
            result = make_result(
                status="ok",
                action=args.command,
                summary=_summary(args.command, parsed),
                details={
                    "device": device,
                    "gdb_port": gdb_port,
                    "commands": gdb_commands,
                    "output": parsed.get("output", ""),
                    "server_output": server_output,
                    "returncode": gdb_result.get("returncode", 0),
                    **{key: value for key, value in parsed.items() if key != "output"},
                },
                context=parameter_context(
                    provider="jlink",
                    workspace=str(workspace),
                    parameter_sources=parameter_sources,
                    config_path=config_path,
                ),
                artifacts=artifacts,
                metrics=_metrics(parsed),
                state=state_info,
                next_actions=["目标已继续运行，如需停下请再次执行 halt/backtrace/run"],
                timing=make_timing(started_at, elapsed_ms),
            )
        elif gdb_result["status"] == "error" or gdb_result["status"] == "timeout":
            result = make_result(
                status="error",
                action=args.command,
                summary="GDB 执行失败",
                details={"device": device, "gdb_port": gdb_port},
                context=parameter_context(
                    provider="jlink",
                    workspace=str(workspace),
                    parameter_sources=parameter_sources,
                    config_path=config_path,
                ),
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
                    "provider": "jlink",
                    "action": args.command,
                    "device": device,
                    "interface": interface or "SWD",
                    "speed": speed or "4000",
                    "serial_no": serial_no or "",
                    "debug_file": elf_file or "",
                    "artifacts": artifacts,
                },
                str(workspace),
            )
            # 写回确认过的参数到工程配置
            save_project_config(str(workspace), {
                "device": device,
                "interface": interface or "SWD",
                "speed": speed or "4000",
            })
            result = make_result(
                status="ok",
                action=args.command,
                summary=_summary(args.command, parsed),
                details={
                    "device": device,
                    "gdb_port": gdb_port,
                    "commands": gdb_commands,
                    "output": parsed.get("output", ""),
                    "server_output": server_output,
                    "returncode": gdb_result.get("returncode", 0),
                    **{key: value for key, value in parsed.items() if key != "output"},
                },
                context=parameter_context(
                    provider="jlink",
                    workspace=str(workspace),
                    parameter_sources=parameter_sources,
                    config_path=config_path,
                ),
                artifacts=artifacts,
                metrics=_metrics(parsed),
                state=state_info,
                next_actions=["可继续基于 last_debug 复用 device/debug_file"],
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
    finally:
        cleanup(procs)


if __name__ == "__main__":
    main()
