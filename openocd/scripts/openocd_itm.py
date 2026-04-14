"""OpenOCD ITM/SWO 观测。

基于 OpenOCD 官方 TPIU/SWO 命令：
- $tpiu_name configure -protocol uart -output :<port> -traceclk <Hz> [-pin-freq <Hz>]
- $tpiu_name enable
- itm port <n> on / itm ports on
"""

from __future__ import annotations

import argparse
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from openocd_runtime import (  # noqa: E402
    default_config_path,
    emit_stream_record,
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
    trace_port: int = 3443,
    tpiu_name: str = "",
    traceclk: str = "",
    pin_freq: str = "",
    itm_ports: list[str] | None = None,
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
    cmd.extend(["-c", f"gdb_port {gdb_port}", "-c", f"telnet_port {telnet_port}"])
    tpiu_cmd = f"{tpiu_name} configure -protocol uart -output :{trace_port} -traceclk {traceclk}"
    if pin_freq:
        tpiu_cmd += f" -pin-freq {pin_freq}"
    cmd.extend(["-c", tpiu_cmd, "-c", "init", "-c", "reset init", "-c", f"{tpiu_name} enable"])
    if itm_ports:
        for port in itm_ports:
            cmd.extend(["-c", f"itm port {port} on"])
    else:
        cmd.extend(["-c", "itm ports on"])
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


def wait_server_ready(proc: subprocess.Popen, trace_port: int, timeout: int = 15) -> tuple[bool, list[str]]:
    started = time.time()
    lines: list[str] = []
    ready = False
    ready_deadline = 0.0
    critical_keywords = [
        "error:",
        "failed to start adapter's trace",
        "not supported by the device",
    ]
    while time.time() - started < timeout:
        if proc.poll() is not None:
            lines.extend(proc.stderr.read().splitlines())
            return False, lines
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.1)
            if ready and time.time() >= ready_deadline:
                return True, lines
            continue
        stripped = line.strip()
        lines.append(stripped)
        lowered = stripped.lower()
        if any(keyword in lowered for keyword in critical_keywords):
            return False, lines
        if f"port {trace_port}" in lowered or "trace data" in lowered:
            ready = True
            ready_deadline = time.time() + 1.0
    return ready, lines


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


def _state_lookup(state: dict) -> dict:
    last_debug = get_state_entry(state, "last_debug")
    last_flash = get_state_entry(state, "last_flash")
    return {
        "board": last_debug.get("board") or last_flash.get("board"),
        "interface": last_debug.get("interface") or last_flash.get("interface"),
        "target": last_debug.get("target") or last_flash.get("target"),
        "search": last_debug.get("search"),
        "adapter_speed": last_debug.get("adapter_speed") or last_flash.get("adapter_speed"),
        "transport": last_debug.get("transport") or last_flash.get("transport"),
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

    # tpiu_name: CLI > 工程配置
    tpiu_name = args.tpiu_name
    tpiu_name_source = "cli"
    if is_missing(tpiu_name):
        tpiu_name = project_config.get("tpiu_name")
        tpiu_name_source = "project_config"

    # traceclk: CLI > 工程配置
    traceclk = args.traceclk
    traceclk_source = "cli"
    if is_missing(traceclk):
        traceclk = project_config.get("traceclk")
        traceclk_source = "project_config"

    # pin_freq: CLI > 工程配置
    pin_freq = args.pin_freq
    pin_freq_source = "cli"
    if is_missing(pin_freq):
        pin_freq = project_config.get("pin_freq")
        pin_freq_source = "project_config"

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
        "tpiu_name": tpiu_name,
        "tpiu_name_source": tpiu_name_source,
        "traceclk": traceclk,
        "traceclk_source": traceclk_source,
        "pin_freq": pin_freq,
        "pin_freq_source": pin_freq_source,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenOCD ITM 输出捕获")
    parser.add_argument("--exe", default=None, help="openocd 路径")
    parser.add_argument("--board", default=None, help="board 配置文件")
    parser.add_argument("--interface", default=None, help="interface 配置文件")
    parser.add_argument("--target", default=None, help="target 配置文件")
    parser.add_argument("--search", default=None, help="额外配置脚本搜索目录")
    parser.add_argument("--adapter-speed", default=None, help="调试速率 kHz")
    parser.add_argument("--transport", default=None, choices=["", "swd", "jtag"], help="传输协议")
    parser.add_argument("--gdb-port", type=int, default=None, help="GDB 端口")
    parser.add_argument("--telnet-port", type=int, default=None, help="Telnet 端口")
    parser.add_argument("--trace-port", type=int, default=3443, help="OpenOCD trace TCP 端口")
    parser.add_argument("--tpiu-name", default=None, help="TPIU/SWO 对象名，例如 stm32l1.tpiu")
    parser.add_argument("--traceclk", default=None, help="TRACECLKIN 频率 Hz")
    parser.add_argument("--pin-freq", default=None, help="SWO pin 频率 Hz")
    parser.add_argument("--itm-port", action="append", dest="itm_ports", help="启用的 ITM stimulus port，可多次传入")
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--config", default=None, help="skill config.json 路径")
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

        # tpiu_name, traceclk, pin_freq 从工程配置解析
        tpiu_name = oc_params["tpiu_name"]
        parameter_sources["tpiu_name"] = oc_params["tpiu_name_source"]
        traceclk = oc_params["traceclk"]
        parameter_sources["traceclk"] = oc_params["traceclk_source"]
        pin_freq = oc_params["pin_freq"]
        parameter_sources["pin_freq"] = oc_params["pin_freq_source"]
    except ValueError as exc:
        result = make_result(
            status="error",
            action="itm",
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
            action="itm",
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

    proc = None
    trace_sock = None
    try:
        proc = start_openocd_server(
            build_openocd_cmd(
                exe=exe,
                board=board or "",
                interface=interface or "",
                target=target or "",
                search=search or "",
                adapter_speed=str(adapter_speed or ""),
                transport=transport or "",
                gdb_port=int(args.gdb_port or config.get("gdb_port", 3333)),
                telnet_port=int(args.telnet_port or config.get("telnet_port", 4444)),
                trace_port=args.trace_port,
                tpiu_name=tpiu_name,
                traceclk=traceclk,
                pin_freq=pin_freq or "",
                itm_ports=args.itm_ports,
            )
        )
        ready, lines = wait_server_ready(proc, args.trace_port)
        if not ready:
            message = "; ".join(line for line in lines if line) or "OpenOCD ITM 初始化失败"
            result = make_result(
                status="error",
                action="itm",
                summary=message,
                details={"server_log": lines},
                context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
                error={"code": "server_failed", "message": message},
                timing=make_timing(started_at, (time.time() - started_ts) * 1000),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {message}", file=sys.stderr)
            sys.exit(1)

        last_trace_error: OSError | None = None
        trace_deadline = time.time() + 5.0
        while time.time() < trace_deadline:
            try:
                trace_sock = socket.create_connection(("127.0.0.1", args.trace_port), timeout=1.0)
                trace_sock.settimeout(0.5)
                last_trace_error = None
                break
            except OSError as exc:
                last_trace_error = exc
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
        if trace_sock is None:
            raise OSError(last_trace_error or "trace socket not ready")

        update_state_entry(
            "last_observe",
            {
                "provider": "openocd",
                "action": "itm",
                "board": board or "",
                "interface": interface or "",
                "target": target or "",
                "search": search or "",
                "adapter_speed": adapter_speed or "",
                "transport": transport or "",
                "channel_type": "itm",
                "stream_type": "binary",
                "source": "openocd",
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
            "tpiu_name": tpiu_name or "",
            "traceclk": traceclk or "",
            "pin_freq": pin_freq or "",
        })

        while True:
            try:
                chunk = trace_sock.recv(4096)
            except socket.timeout:
                if proc.poll() is not None:
                    break
                continue
            if not chunk:
                if proc.poll() is not None:
                    break
                continue
            text = chunk.decode("utf-8", errors="replace")
            emit_stream_record(source="openocd", channel_type="itm", text=text, as_json=args.as_json, stream_type="event")

    except FileNotFoundError:
        message = f"openocd 不存在或不在 PATH 中: {exe}"
        result = make_result(
            status="error",
            action="itm",
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
    except OSError as exc:
        message = f"ITM trace 连接失败: {exc}"
        result = make_result(
            status="error",
            action="itm",
            summary=message,
            details={},
            context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "trace_connect_failed", "message": message},
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
        if trace_sock:
            trace_sock.close()
        cleanup(proc)


if __name__ == "__main__":
    main()
