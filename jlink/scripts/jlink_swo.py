"""J-Link SWO 观测包装层。

说明：
- J-Link 不同版本的 SWO CLI 工具名和参数差异较大。
- 本脚本不硬编码具体 viewer，可通过 --viewer-cmd 或 config.json 中的 swo_command 提供完整命令。
"""

from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from jlink_runtime import (  # noqa: E402
    default_config_path,
    emit_stream_record,
    get_state_entry,
    hidden_subprocess_kwargs,
    load_json_file,
    load_project_config,
    load_workspace_state,
    make_result,
    make_timing,
    normalize_path,
    now_iso,
    output_json,
    parameter_context,
    resolve_tool_param,
    update_state_entry,
    workspace_root,
)


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


def _auto_viewer_cmd(config: dict, project_config: dict, state: dict) -> list[str]:
    jlink_exe, _ = resolve_tool_param(
        "exe",
        None,
        local_config=config,
        local_keys=["exe"],
        path_candidates=["JLink.exe"],
        default="JLink.exe",
    )
    if not jlink_exe:
        return []

    exe_path = Path(str(jlink_exe)).expanduser()
    viewer = exe_path.with_name("JLinkSWOViewerCL.exe")
    if not viewer.exists():
        return []

    last_debug = get_state_entry(state, "last_debug")
    last_flash = get_state_entry(state, "last_flash")
    device = project_config.get("device") or last_debug.get("device") or last_flash.get("device")
    if not device:
        return []

    # JLinkSWOViewerCL 不同版本支持的参数差异很大。
    # 这里退回到最稳妥的最小命令，只传 device，避免因 -itf/-speed 不兼容直接失败。
    return [str(viewer), "-device", str(device)]


def main() -> None:
    parser = argparse.ArgumentParser(description="J-Link SWO 输出捕获")
    parser.add_argument("--viewer-cmd", nargs="+", default=None, help="完整 SWO 采集命令，例如 JLinkSWOViewerCL 的调用参数")
    parser.add_argument("--duration", type=float, default=0, help="采集时长(秒)，0=持续运行")
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--config", default=None, help="skill config.json 路径")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args, passthrough = parser.parse_known_args()

    started_at = now_iso()
    started_ts = time.time()
    workspace = workspace_root(args.workspace)
    config_path = normalize_path(args.config or str(default_config_path(__file__)))
    config = load_json_file(config_path)
    project_config = load_project_config(str(workspace))
    state = load_workspace_state(str(workspace))
    viewer_cmd = list(args.viewer_cmd or config.get("swo_command") or _auto_viewer_cmd(config, project_config, state))
    viewer_cmd.extend(passthrough)

    if not viewer_cmd:
        message = "缺少 SWO viewer 命令，请通过 --viewer-cmd 或 jlink/config.json.swo_command 提供"
        result = make_result(
            status="error",
            action="swo",
            summary=message,
            details={},
            context=parameter_context(provider="jlink", workspace=str(workspace), config_path=config_path),
            error={"code": "missing_viewer_cmd", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)

    proc = None
    try:
        proc = subprocess.Popen(
            viewer_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(workspace),
            **hidden_subprocess_kwargs(),
        )
        line_queue = start_stream_reader(proc.stdout)
        update_state_entry(
            "last_observe",
            {
                "provider": "jlink",
                "action": "swo",
                "channel_type": "swo",
                "stream_type": "text",
                "source": "jlink",
            },
            str(workspace),
        )

        while True:
            if args.duration > 0 and (time.time() - started_ts) >= args.duration:
                break
            try:
                line = line_queue.get(timeout=0.1)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            if line is None:
                if proc.poll() is not None:
                    break
                continue
            emit_stream_record(source="jlink", channel_type="swo", text=line, as_json=args.as_json, stream_type="text")

    except FileNotFoundError:
        message = f"无法启动 SWO viewer: {viewer_cmd[0]}"
        result = make_result(
            status="error",
            action="swo",
            summary=message,
            details={"viewer_cmd": viewer_cmd},
            context=parameter_context(provider="jlink", workspace=str(workspace), config_path=config_path),
            error={"code": "viewer_not_found", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    finally:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
            if proc.poll() is None and sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **hidden_subprocess_kwargs(),
                )
            elif proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    main()
