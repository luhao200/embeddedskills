"""probe-rs GDB Server 启动与 one-shot 调试。"""

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

from probe_rs_gdb_common import build_gdb_commands, parse_gdb_output, run_gdb_commands  # noqa: E402
from probe_rs_runtime import (  # noqa: E402
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
    resolve_artifact_param,
    resolve_project_param,
    resolve_tool_param,
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


def start_gdb_server(exe: str, chip: str, protocol: str, speed: str, probe: str, connect_under_reset: bool, gdb_port: int) -> tuple[subprocess.Popen, int]:
    if not gdb_port:
        gdb_port = find_free_port()

    cmd = [
        exe,
        "gdb",
        "--non-interactive",
        "--chip",
        chip,
        "--protocol",
        protocol,
        "--speed",
        speed,
        "--gdb-connection-string",
        f"localhost:{gdb_port}",
    ]
    if probe:
        cmd.extend(["--probe", probe])
    if connect_under_reset:
        cmd.append("--connect-under-reset")

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


def wait_gdb_server_ready(proc: subprocess.Popen, port: int, timeout: int = 15) -> tuple[bool, str]:
    started = time.time()
    startup_grace = min(5.0, max(1.0, timeout / 3))
    while time.time() - started < timeout:
        if proc.poll() is not None:
            stdout = proc.stdout.read() if proc.stdout else ""
            stderr = proc.stderr.read() if proc.stderr else ""
            return False, "\n".join(part for part in (stdout, stderr) if part).strip()
        if time.time() - started >= startup_grace:
            return True, f"probe-rs gdb 已启动，假定 localhost:{port} 可用"
        time.sleep(0.2)
    return False, f"GDB Server 在 {timeout}s 内未监听 localhost:{port}"


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


def _state_lookup(state: dict) -> dict:
    last_build = get_state_entry(state, "last_build")
    last_flash = get_state_entry(state, "last_flash")
    last_debug = get_state_entry(state, "last_debug")
    artifacts = last_build.get("artifacts", {})
    return {
        "chip": last_debug.get("chip") or last_flash.get("chip"),
        "probe": last_debug.get("probe") or last_flash.get("probe"),
        "protocol": last_debug.get("protocol") or last_flash.get("protocol"),
        "speed": last_debug.get("speed") or last_flash.get("speed"),
        "connect_under_reset": last_debug.get("connect_under_reset") or last_flash.get("connect_under_reset"),
        "elf_file": last_build.get("debug_file") or last_build.get("elf_file") or artifacts.get("debug_file"),
    }


def resolve_probe_params(args, config: dict, project_config: dict, state_lookup: dict) -> tuple[dict, dict]:
    parameter_sources: dict[str, str] = {}

    exe, parameter_sources["exe"] = resolve_tool_param(
        "exe",
        args.exe,
        local_config=config,
        local_keys=["exe"],
        path_candidates=["probe-rs.exe", "probe-rs"],
        default="probe-rs",
    )
    gdb_exe, parameter_sources["gdb_exe"] = resolve_tool_param(
        "gdb_exe",
        args.gdb_exe,
        local_config=config,
        local_keys=["gdb_exe"],
        path_candidates=["arm-none-eabi-gdb.exe", "arm-none-eabi-gdb"],
        default="arm-none-eabi-gdb",
    )
    chip, parameter_sources["chip"] = resolve_project_param(
        "chip",
        args.chip,
        project_config=project_config,
        project_keys=["chip"],
        state_record=state_lookup,
        state_keys=["chip"],
    )
    protocol, parameter_sources["protocol"] = resolve_project_param(
        "protocol",
        args.protocol,
        project_config=project_config,
        project_keys=["protocol"],
        state_record=state_lookup,
        state_keys=["protocol"],
        default="swd",
    )
    probe, parameter_sources["probe"] = resolve_project_param(
        "probe",
        args.probe,
        project_config=project_config,
        project_keys=["probe"],
        state_record=state_lookup,
        state_keys=["probe"],
    )
    speed, parameter_sources["speed"] = resolve_project_param(
        "speed",
        args.speed,
        project_config=project_config,
        project_keys=["speed"],
        state_record=state_lookup,
        state_keys=["speed"],
        default="4000",
    )

    connect_under_reset = args.connect_under_reset
    connect_source = "cli" if args.connect_under_reset else ""
    if not connect_under_reset:
        value = project_config.get("connect_under_reset")
        if value is not None:
            connect_under_reset = bool(value)
            connect_source = "project_config"
    if not connect_under_reset:
        value = state_lookup.get("connect_under_reset")
        if value is not None:
            connect_under_reset = bool(value)
            connect_source = "state"
    if not connect_source:
        connect_source = "default"
    parameter_sources["connect_under_reset"] = connect_source

    elf_file, parameter_sources["elf"] = resolve_artifact_param(
        "elf",
        args.elf,
        project_config=project_config,
        project_keys=["elf", "default_elf"],
        state_record=state_lookup,
        state_keys=["elf_file"],
        normalize_as_path=True,
    )

    gdb_port = args.gdb_port if args.gdb_port else config.get("gdb_port", 3333)
    parameter_sources["gdb_port"] = "cli" if args.gdb_port else ("config:gdb_port" if config.get("gdb_port") else "default")

    return (
        {
            "exe": exe,
            "gdb_exe": gdb_exe,
            "chip": chip,
            "protocol": protocol,
            "probe": probe,
            "speed": str(speed),
            "connect_under_reset": bool(connect_under_reset),
            "elf_file": elf_file,
            "gdb_port": int(gdb_port or 0),
        },
        parameter_sources,
    )


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


def stepping_fallback_commands(action: str) -> list[str] | None:
    if action == "next":
        return ["monitor halt", "nexti"]
    if action == "step":
        return ["monitor halt", "stepi"]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="probe-rs GDB Server 调试")
    sub = parser.add_subparsers(dest="command")
    for name in ALL_COMMANDS:
        sub_parser = sub.add_parser(name, help=f"GDB {name}")
        sub_parser.add_argument("--exe", default=None, help="probe-rs 可执行文件")
        sub_parser.add_argument("--gdb-exe", default=None, help="arm-none-eabi-gdb 路径")
        sub_parser.add_argument("--chip", default=None, help="芯片型号")
        sub_parser.add_argument("--elf", default=None, help="ELF 文件路径")
        sub_parser.add_argument("--protocol", default=None, choices=["swd", "jtag"], help="调试协议")
        sub_parser.add_argument("--probe", default=None, help="探针选择器")
        sub_parser.add_argument("--speed", default=None, help="调试速率 kHz")
        sub_parser.add_argument("--connect-under-reset", action="store_true", help="连接时保持 reset")
        sub_parser.add_argument("--gdb-port", type=int, default=0, help="GDB 端口，0=自动")
        sub_parser.add_argument("--config", default=None, help="skill config.json 路径")
        sub_parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
        sub_parser.add_argument("--json", action="store_true", dest="as_json")
        if name == "run":
            sub_parser.add_argument("--commands", nargs="+", required=True, help="GDB 命令序列")
        elif name in {"break", "frame", "print", "watch"}:
            sub_parser.add_argument("--expr", required=True, help="表达式或参数")
        elif name in {"until", "disassemble"}:
            sub_parser.add_argument("--expr", default=None, help="表达式或参数")

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

    params, parameter_sources = resolve_probe_params(args, config, project_config, state_lookup)
    if is_missing(params["chip"]):
        result = make_result(
            status="error",
            action=args.command,
            summary="缺少必要参数: chip",
            context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "missing_chip", "message": "必须提供 --chip，或通过 .embeddedskills/config.json 的 probe-rs 段配置"},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    if is_missing(params["gdb_exe"]) or not os.path.isfile(str(params["gdb_exe"])):
        message = f"arm-none-eabi-gdb 不存在: {params['gdb_exe']}"
        result = make_result(
            status="error",
            action=args.command,
            summary=message,
            context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "gdb_not_found", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)

    try:
        gdb_commands = list(args.commands) if args.command == "run" else build_gdb_commands(args.command, getattr(args, "expr", None))
    except ValueError as exc:
        result = make_result(
            status="error",
            action=args.command,
            summary=str(exc),
            context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "invalid_args", "message": str(exc)},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    procs: list[subprocess.Popen] = []
    try:
        server_proc, gdb_port = start_gdb_server(
            exe=params["exe"],
            chip=params["chip"],
            protocol=params["protocol"],
            speed=params["speed"],
            probe=params["probe"] or "",
            connect_under_reset=params["connect_under_reset"],
            gdb_port=params["gdb_port"],
        )
        procs.append(server_proc)

        ready, server_output = wait_gdb_server_ready(server_proc, gdb_port)
        if not ready:
            result = make_result(
                status="error",
                action=args.command,
                summary="GDB Server 启动失败",
                details={"chip": params["chip"], "server_output": server_output},
                context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
                error={"code": "gdbserver_failed", "message": server_output or "GDB Server 启动失败"},
                timing=make_timing(started_at, (time.time() - started_ts) * 1000),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"[probe-rs gdb-{args.command}] 失败 — {result['error']['message']}", file=sys.stderr)
            sys.exit(1)

        gdb_result = run_gdb_commands(str(params["gdb_exe"]), params["elf_file"] or "", f"localhost:{gdb_port}", gdb_commands)
        if gdb_result["status"] == "timeout":
            fallback_commands = stepping_fallback_commands(args.command)
            if fallback_commands:
                gdb_result = run_gdb_commands(str(params["gdb_exe"]), params["elf_file"] or "", f"localhost:{gdb_port}", fallback_commands)
                if gdb_result["status"] == "ok":
                    gdb_commands = fallback_commands
        elapsed_ms = (time.time() - started_ts) * 1000

        if gdb_result["status"] == "timeout" and args.command == "continue":
            parsed = parse_gdb_output(gdb_result.get("stdout", ""), args.command)
            parsed["timed_out"] = True
        elif gdb_result["status"] in {"error", "timeout"}:
            result = make_result(
                status="error",
                action=args.command,
                summary="GDB 执行失败",
                details={"chip": params["chip"], "gdb_port": gdb_port, "server_output": server_output},
                context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
                artifacts=build_artifacts(debug_file=params["elf_file"]),
                error={"code": "gdb_error", "message": gdb_result.get("error", gdb_result.get("stderr", "GDB 执行失败"))},
                timing=make_timing(started_at, elapsed_ms),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"[probe-rs gdb-{args.command}] 失败 — {result['error']['message']}", file=sys.stderr)
            sys.exit(1)
        else:
            parsed = parse_gdb_output(gdb_result["stdout"], args.command)

        artifacts = build_artifacts(debug_file=params["elf_file"])
        state_info = update_state_entry(
            "last_debug",
            {
                "provider": "probe-rs",
                "action": args.command,
                "chip": params["chip"],
                "probe": params["probe"] or "",
                "protocol": params["protocol"],
                "speed": params["speed"],
                "connect_under_reset": params["connect_under_reset"],
                "debug_file": params["elf_file"] or "",
                "artifacts": artifacts,
            },
            str(workspace),
        )
        save_project_config(str(workspace), {
            "chip": params["chip"],
            "protocol": params["protocol"],
            "probe": params["probe"] or "",
            "speed": params["speed"],
            "connect_under_reset": params["connect_under_reset"],
        })
        result = make_result(
            status="ok",
            action=args.command,
            summary=_summary(args.command, parsed),
            details={
                "chip": params["chip"],
                "probe": params["probe"] or "",
                "protocol": params["protocol"],
                "gdb_port": gdb_port,
                "commands": gdb_commands,
                "output": parsed.get("output", ""),
                "server_output": server_output,
                "returncode": gdb_result.get("returncode", 0),
                **{key: value for key, value in parsed.items() if key != "output"},
            },
            context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            artifacts=artifacts,
            metrics=_metrics(parsed),
            state=state_info,
            next_actions=["可继续基于 last_debug 复用 chip/debug_file"],
            timing=make_timing(started_at, elapsed_ms),
        )

        if args.as_json:
            output_json(result)
        elif result["status"] == "ok":
            print(f"[probe-rs gdb-{args.command}] {result['summary']}")
            output = result.get("details", {}).get("output", "")
            if output:
                print(output)
        else:
            print(f"[probe-rs gdb-{args.command}] 失败 — {result['error']['message']}", file=sys.stderr)
            sys.exit(1)
    finally:
        cleanup(procs)


if __name__ == "__main__":
    main()
