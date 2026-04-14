"""can skill 私有运行时工具。"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_NAME = "can"
STATE_DIR_NAME = ".embeddedskills"
STATE_FILE_NAME = "state.json"
PROJECT_CONFIG_FILE = "config.json"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def is_missing(value: Any) -> bool:
    return value is None or value == ""


def load_json_file(path: str | Path) -> dict:
    """加载 JSON 文件，不存在返回空字典"""
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_json_file(path: str | Path, data: dict) -> None:
    """保存 JSON 文件，自动创建目录"""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_local_config() -> dict:
    """加载 skill/config.json（环境级配置）"""
    return load_json_file(SKILL_DIR / "config.json")


def save_local_config(data: dict) -> None:
    """保存环境级配置到 skill/config.json"""
    save_json_file(SKILL_DIR / "config.json", data)


def workspace_root(workspace: str | None = None) -> Path:
    if not is_missing(workspace):
        return Path(str(workspace)).expanduser().resolve()
    return Path.cwd().resolve()


def add_can_connection_args(parser, *, include_data_bitrate: bool = False) -> None:
    """为 CLI 补充通用 CAN 连接参数。"""
    parser.add_argument("--interface", help="CAN 后端接口名，如 pcan、vector、socketcan、slcan")
    parser.add_argument("--channel", help="接口通道，如 PCAN_USBBUS1、0、can0")
    parser.add_argument("--bitrate", type=int, help="CAN 仲裁域波特率，默认 500000")
    if include_data_bitrate:
        parser.add_argument("--data-bitrate", dest="data_bitrate", type=int, help="CAN-FD 数据域波特率，默认 2000000")


def load_project_config(workspace: str | None = None) -> dict:
    """从 workspace/.embeddedskills/config.json 读取本 skill 的工程级配置"""
    proj_config = load_json_file(workspace_root(workspace) / STATE_DIR_NAME / PROJECT_CONFIG_FILE)
    return proj_config.get(SKILL_NAME, {})


def save_project_config(workspace: str | None = None, values: dict | None = None) -> None:
    """写回工程级配置，只更新本 skill 的部分"""
    if values is None:
        return
    proj_path = workspace_root(workspace) / STATE_DIR_NAME / PROJECT_CONFIG_FILE
    proj_config = load_json_file(proj_path)
    proj_config[SKILL_NAME] = {**proj_config.get(SKILL_NAME, {}), **values}
    save_json_file(proj_path, proj_config)


def load_workspace_state(workspace: str | None = None) -> dict:
    """从 workspace/.embeddedskills/state.json 读取状态"""
    return load_json_file(workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME)


def save_workspace_state(state: dict, workspace: str | None = None) -> Path:
    """保存状态"""
    file_path = workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME
    save_json_file(file_path, state)
    return file_path


def update_state_entry(category: str, record: dict, workspace: str | None = None) -> dict:
    """更新状态条目"""
    state = load_workspace_state(workspace)
    state[category] = {**record, "timestamp": record.get("timestamp") or now_iso()}
    file_path = save_workspace_state(state, workspace)
    return {
        "workspace": str(workspace_root(workspace)),
        "file": str(file_path),
        "updated_keys": [category],
        category: state[category],
    }


def normalize_path(value: str | None, base: str | Path | None = None) -> str:
    """路径规范化"""
    if is_missing(value):
        return ""
    path = Path(str(value)).expanduser()
    if base and not path.is_absolute():
        path = Path(base) / path
    return str(path.resolve()) if path.is_absolute() else str(path)


def _first_resolved(mapping: dict, keys: list[str]) -> tuple[Any, str | None]:
    for key in keys:
        value = mapping.get(key)
        if not is_missing(value):
            return value, key
    return None, None


def resolve_param(
    name: str,
    cli_value: Any = None,
    local_config: dict | None = None,
    local_keys: list[str] | None = None,
    project_config: dict | None = None,
    project_keys: list[str] | None = None,
    state: dict | None = None,
    state_keys: list[str] | None = None,
    default: Any = None,
) -> tuple[Any, str]:
    """统一参数解析，优先级: CLI > 环境级 > 工程级 > state > default"""
    if not is_missing(cli_value):
        return cli_value, "cli"

    if local_config and local_keys:
        value, key = _first_resolved(local_config, local_keys)
        if not is_missing(value):
            return value, f"local:{key}"

    if project_config and project_keys:
        value, key = _first_resolved(project_config, project_keys)
        if not is_missing(value):
            return value, f"project:{key}"

    if state and state_keys:
        value, key = _first_resolved(state, state_keys)
        if not is_missing(value):
            return value, f"state:{key}"

    if not is_missing(default):
        return default, "default"

    return None, ""


def parameter_context(name: str, value: Any, source: str) -> dict:
    """记录参数来源"""
    return {"name": name, "value": value, "source": source}


def make_result(
    success: bool = True,
    action: str = "",
    summary: str = "",
    details: dict | None = None,
    error: dict | None = None,
) -> dict:
    """统一结果格式"""
    result = {
        "status": "ok" if success else "error",
        "action": action,
        "summary": summary,
    }
    if details:
        result["details"] = details
    if error:
        result["error"] = error
    return result


def make_timing(start_time: float) -> dict:
    """执行时间记录"""
    elapsed = datetime.now().timestamp() - start_time
    return {
        "started_at": datetime.fromtimestamp(start_time).astimezone().isoformat(timespec="seconds"),
        "finished_at": now_iso(),
        "elapsed_ms": int(elapsed * 1000),
    }


def load_known_devices() -> tuple[list, list]:
    """加载已知的 CAN 设备和接口"""
    try:
        common_path = SKILL_DIR / "references" / "common_interfaces.json"
        data = json.loads(common_path.read_text(encoding="utf-8"))
        return data.get("usb_can_devices", []), data.get("known_interfaces", [])
    except Exception:
        return [], []


def check_interface_available(interface_name: str) -> bool:
    """尝试导入对应后端，判断是否可用"""
    try:
        from can.interfaces import VALID_INTERFACES
        return interface_name in VALID_INTERFACES
    except Exception:
        return False


def scan_usb_can_devices() -> list[dict]:
    """扫描 USB-CAN 设备"""
    known_devices, _ = load_known_devices()
    if not known_devices:
        return []

    found = []
    is_windows = platform.system() == "Windows"

    if is_windows:
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-PnpDevice -Class USB -Status OK | Select-Object -Property InstanceId,FriendlyName | ConvertTo-Json"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                devices = json.loads(result.stdout)
                if isinstance(devices, dict):
                    devices = [devices]
                for dev in devices:
                    instance_id = dev.get("InstanceId", "").upper()
                    friendly = dev.get("FriendlyName", "")
                    for known in known_devices:
                        vid = known["vid"].upper()
                        pid = known["pid"].upper()
                        if f"VID_{vid}" in instance_id and f"PID_{pid}" in instance_id:
                            found.append({
                                "name": known["name"],
                                "vid": vid,
                                "pid": pid,
                                "interface": known["interface"],
                                "channel": known["channel"],
                                "friendly_name": friendly,
                            })
        except Exception:
            pass
    else:
        # Linux: 检查 lsusb
        try:
            result = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    line_upper = line.upper()
                    for known in known_devices:
                        vid = known["vid"].upper()
                        pid = known["pid"].upper()
                        if f"{vid}:{pid}" in line_upper:
                            found.append({
                                "name": known["name"],
                                "vid": vid,
                                "pid": pid,
                                "interface": known["interface"],
                                "channel": known["channel"],
                                "friendly_name": line.strip(),
                            })
        except Exception:
            pass

    return found


def scan_socketcan() -> list[dict]:
    """Linux: 扫描 SocketCAN 接口"""
    if platform.system() != "Linux":
        return []
    interfaces = []
    try:
        result = subprocess.run(
            ["ip", "-j", "link", "show", "type", "can"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            for iface in json.loads(result.stdout):
                interfaces.append({
                    "interface": "socketcan",
                    "channel": iface.get("ifname", ""),
                    "status": iface.get("operstate", "unknown").lower(),
                })
    except Exception:
        pass
    return interfaces


def scan_can_interfaces() -> tuple[list[dict], str | None]:
    """扫描所有可用 CAN 接口"""
    try:
        import can  # noqa: F401
    except ImportError:
        return [], "python-can 未安装，请执行 pip install python-can"

    results = []

    # 1. 扫描 USB-CAN 设备
    for dev in scan_usb_can_devices():
        results.append({
            "interface": dev["interface"],
            "channel": dev["channel"],
            "device": dev["name"],
            "vid": dev["vid"],
            "pid": dev["pid"],
            "status": "detected",
        })

    # 2. 扫描 SocketCAN
    for iface in scan_socketcan():
        results.append({
            "interface": iface["interface"],
            "channel": iface["channel"],
            "device": "",
            "vid": "",
            "pid": "",
            "status": iface["status"],
        })

    # 3. 检查已知后端可用性
    _, known_interfaces = load_known_devices()
    backends_found = {r["interface"] for r in results}
    for ki in known_interfaces:
        iface_name = ki["interface"]
        if iface_name not in backends_found:
            if check_interface_available(iface_name):
                results.append({
                    "interface": iface_name,
                    "channel": "",
                    "device": "",
                    "vid": "",
                    "pid": "",
                    "status": "backend_available",
                })

    return results, None


def get_can_config(
    cli_interface: str | None = None,
    cli_channel: str | None = None,
    cli_bitrate: int | None = None,
    cli_data_bitrate: int | None = None,
    workspace: str | None = None,
) -> tuple[dict, dict]:
    """
    获取 CAN 配置，按优先级解析参数。
    返回 (config_dict, sources_dict)
    """
    local_cfg = load_local_config()
    proj_cfg = load_project_config(workspace)
    state = load_workspace_state(workspace)

    sources = {}

    # 解析各个参数
    interface, src = resolve_param(
        "interface", cli_interface,
        project_config=proj_cfg, project_keys=["interface"],
        state=state, state_keys=["last_can_interface"],
    )
    sources["interface"] = src or "unknown"

    channel, src = resolve_param(
        "channel", cli_channel,
        project_config=proj_cfg, project_keys=["channel"],
        state=state, state_keys=["last_can_channel"],
    )
    sources["channel"] = src or "unknown"

    bitrate, src = resolve_param(
        "bitrate", cli_bitrate,
        project_config=proj_cfg, project_keys=["bitrate"],
        state=state, state_keys=["last_can_bitrate"],
        default=500000,
    )
    sources["bitrate"] = src or "default"

    data_bitrate, src = resolve_param(
        "data_bitrate", cli_data_bitrate,
        project_config=proj_cfg, project_keys=["data_bitrate"],
        state=state, state_keys=["last_can_data_bitrate"],
        default=2000000,
    )
    sources["data_bitrate"] = src or "default"

    # 如果没有指定 interface/channel，尝试扫描
    if is_missing(interface) or is_missing(channel):
        interfaces, err = scan_can_interfaces()
        if err:
            return None, {"error": err}
        if len(interfaces) == 1:
            # 唯一候选，自动写入配置
            iface = interfaces[0]
            interface = iface["interface"]
            channel = iface["channel"]
            sources["interface"] = "auto_scan"
            sources["channel"] = "auto_scan"
            save_project_config(workspace, {
                "interface": interface,
                "channel": channel,
            })
        elif len(interfaces) > 1:
            return None, {
                "error": "找到多个 CAN 接口，请指定一个",
                "candidates": interfaces,
                "need_selection": True,
            }
        else:
            return None, {"error": "未找到可用 CAN 接口"}

    log_dir, src = resolve_param(
        "log_dir", None,
        project_config=proj_cfg, project_keys=["log_dir"],
        default=".embeddedskills/logs/can",
    )
    sources["log_dir"] = src or "default"

    # 获取 slcan 相关环境级配置
    slcan_serial_port = local_cfg.get("slcan_serial_port", "")
    slcan_serial_baudrate = local_cfg.get("slcan_serial_baudrate", 115200)

    config = {
        "interface": interface,
        "channel": channel,
        "bitrate": bitrate,
        "data_bitrate": data_bitrate,
        "log_dir": log_dir,
        "slcan_serial_port": slcan_serial_port,
        "slcan_serial_baudrate": slcan_serial_baudrate,
    }

    return config, sources


def open_can_bus(config: dict):
    """根据配置打开 CAN 总线"""
    import can

    bus_kwargs = {
        "interface": config["interface"],
        "channel": config["channel"],
    }
    if config.get("bitrate"):
        bus_kwargs["bitrate"] = config["bitrate"]

    return can.Bus(**bus_kwargs)


def output_json(data: dict, *, indent: int = 2) -> None:
    """输出 JSON 到 stdout"""
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=indent), flush=True)
