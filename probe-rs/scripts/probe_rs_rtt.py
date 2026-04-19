"""probe-rs RTT 日志读取。"""

from __future__ import annotations

import argparse
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from probe_rs_runtime import (  # noqa: E402
    default_config_path,
    emit_stream_record,
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


def start_stream_reader(stream) -> queue.Queue:
    line_queue: queue.Queue = queue.Queue()

    def _reader() -> None:
        try:
            for line in iter(stream.readline, ""):
                line_queue.put(line)
        finally:
            line_queue.put(None)

    threading.Thread(target=_reader, daemon=True).start()
    return line_queue


def _state_lookup(state: dict) -> dict:
    last_build = get_state_entry(state, "last_build")
    last_debug = get_state_entry(state, "last_debug")
    last_flash = get_state_entry(state, "last_flash")
    artifacts = last_build.get("artifacts", {})
    return {
        "chip": last_debug.get("chip") or last_flash.get("chip"),
        "probe": last_debug.get("probe") or last_flash.get("probe"),
        "protocol": last_debug.get("protocol") or last_flash.get("protocol"),
        "speed": last_debug.get("speed") or last_flash.get("speed"),
        "connect_under_reset": last_debug.get("connect_under_reset") or last_flash.get("connect_under_reset"),
        "elf_file": last_build.get("debug_file") or artifacts.get("debug_file"),
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

    return (
        {
            "exe": exe,
            "chip": chip,
            "protocol": protocol,
            "probe": probe,
            "speed": str(speed),
            "connect_under_reset": bool(connect_under_reset),
            "elf_file": elf_file,
        },
        parameter_sources,
    )


def build_attach_command(params: dict) -> list[str]:
    if is_missing(params["chip"]):
        raise ValueError("缺少必要参数: chip")
    cmd = [
        params["exe"],
        "attach",
        "--non-interactive",
        "--chip",
        params["chip"],
        "--protocol",
        params["protocol"],
        "--speed",
        params["speed"],
    ]
    if params["probe"]:
        cmd.extend(["--probe", params["probe"]])
    if params["connect_under_reset"]:
        cmd.append("--connect-under-reset")
    if params["elf_file"]:
        cmd.append(params["elf_file"])
    return cmd


def main() -> None:
    parser = argparse.ArgumentParser(description="probe-rs RTT 日志读取")
    parser.add_argument("--exe", default=None, help="probe-rs 可执行文件")
    parser.add_argument("--chip", default=None, help="芯片型号")
    parser.add_argument("--elf", default=None, help="ELF 文件路径")
    parser.add_argument("--protocol", default=None, choices=["swd", "jtag"], help="调试协议")
    parser.add_argument("--probe", default=None, help="探针选择器")
    parser.add_argument("--speed", default=None, help="调试速率 kHz")
    parser.add_argument("--connect-under-reset", action="store_true", help="连接时保持 reset")
    parser.add_argument("--duration", type=float, default=0, help="读取时长(秒)，0=持续运行")
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

    params, parameter_sources = resolve_probe_params(args, config, project_config, state_lookup)
    try:
        cmd = build_attach_command(params)
    except ValueError as exc:
        result = make_result(
            status="error",
            action="rtt",
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

    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(new_process_group=True),
        )
    except FileNotFoundError:
        result = make_result(
            status="error",
            action="rtt",
            summary=f"probe-rs 不存在或不在 PATH 中: {params['exe']}",
            context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "exe_not_found", "message": f"probe-rs 不存在或不在 PATH 中: {params['exe']}"},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    stdout_queue = start_stream_reader(proc.stdout)
    stderr_queue = start_stream_reader(proc.stderr)

    state_info = update_state_entry(
        "last_observe",
        {
            "provider": "probe-rs",
            "action": "rtt",
            "chip": params["chip"],
            "probe": params["probe"] or "",
            "protocol": params["protocol"],
            "speed": params["speed"],
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

    if args.as_json:
        header = make_result(
            status="ok",
            action="rtt",
            summary="probe-rs RTT 已启动",
            details={"command": cmd, "chip": params["chip"], "probe": params["probe"] or ""},
            context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            state=state_info,
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        output_json(header)
    else:
        print(f"[probe-rs rtt] 已启动，chip={params['chip']}")

    deadline = time.time() + args.duration if args.duration > 0 else None
    stdout_done = False
    stderr_done = False
    lines = 0

    try:
        while True:
            if deadline and time.time() >= deadline:
                break
            if proc.poll() is not None and stdout_done and stderr_done:
                break

            handled = False
            try:
                item = stdout_queue.get(timeout=0.1)
                handled = True
                if item is None:
                    stdout_done = True
                else:
                    lines += 1
                    emit_stream_record(source="probe-rs", channel_type="rtt", text=item, as_json=args.as_json)
            except queue.Empty:
                pass

            try:
                item = stderr_queue.get_nowait()
                handled = True
                if item is None:
                    stderr_done = True
                else:
                    emit_stream_record(source="probe-rs", channel_type="rtt", stream_type="stderr", text=item, as_json=args.as_json)
            except queue.Empty:
                pass

            if not handled:
                time.sleep(0.05)
    finally:
        cleanup(proc)

    elapsed_ms = (time.time() - started_ts) * 1000
    if args.as_json:
        footer = make_result(
            status="ok",
            action="rtt",
            summary="probe-rs RTT 已结束",
            details={"chip": params["chip"], "lines": lines},
            context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            metrics={"lines": lines},
            timing=make_timing(started_at, elapsed_ms),
        )
        output_json(footer)
    else:
        print(f"[probe-rs rtt] 已结束，lines={lines}")


if __name__ == "__main__":
    main()
