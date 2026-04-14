"""gcc skill 私有运行时工具。"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


STATE_DIR_NAME = ".embeddedskills"
STATE_FILE_NAME = "state.json"
PROJECT_CONFIG_FILE_NAME = "config.json"

# Skill name for project config
SKILL_NAME = "gcc"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_config_path(script_file: str) -> Path:
    return Path(script_file).resolve().parents[1] / "config.json"


def load_local_config(script_file: str | None = None) -> dict:
    """加载 skill/config.json（环境级配置）
    
    路径：当前脚本所在 skill 目录下的 config.json
    """
    if script_file is None:
        # 获取调用者的文件路径
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            script_file = frame.f_back.f_globals.get("__file__", "")
        if not script_file:
            return {}
    config_path = default_config_path(script_file)
    return load_json_file(config_path)


def save_local_config(data: dict, script_file: str | None = None) -> Path | None:
    """保存环境级配置到 skill/config.json"""
    if script_file is None:
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            script_file = frame.f_back.f_globals.get("__file__", "")
        if not script_file:
            return None
    config_path = default_config_path(script_file)
    existing = load_json_file(config_path)
    existing.update(data)
    save_json_file(config_path, existing)
    return config_path


def load_project_config(workspace: str | None = None) -> dict:
    """从 workspace/.embeddedskills/config.json 读取本 skill 的工程级配置
    
    参数: workspace - 工作区路径，None 时使用 cwd
    返回: 该 skill 对应的配置字典（如 config["keil"] 或 config["gcc"]）
    """
    ws = workspace_root(workspace)
    config_file = ws / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME
    data = load_json_file(config_file)
    return data.get(SKILL_NAME, {})


def save_project_config(workspace: str | None = None, values: dict | None = None) -> Path | None:
    """写回工程级配置到 workspace/.embeddedskills/config.json
    
    - 只更新本 skill 的配置部分，不覆盖其他 skill 的配置
    - 目录不存在时自动创建 .embeddedskills/
    """
    if values is None:
        values = {}
    ws = workspace_root(workspace)
    config_file = ws / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME
    data = load_json_file(config_file)
    data[SKILL_NAME] = {**(data.get(SKILL_NAME, {})), **values}
    save_json_file(config_file, data)
    return config_file


def output_json(data: dict, *, indent: int = 2) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=indent), flush=True)


def is_missing(value: Any) -> bool:
    return value is None or value == ""


def normalize_path(value: str | None) -> str:
    if is_missing(value):
        return ""
    return str(Path(str(value)).expanduser().resolve())


def load_json_file(path: str | Path) -> dict:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_json_file(path: str | Path, data: dict) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def hidden_subprocess_kwargs() -> dict:
    if sys.platform != "win32":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def workspace_root(workspace: str | None = None) -> Path:
    if not is_missing(workspace):
        return Path(str(workspace)).expanduser().resolve()
    return Path.cwd().resolve()


def load_workspace_state(workspace: str | None = None) -> dict:
    return load_json_file(workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME)


def save_workspace_state(state: dict, workspace: str | None = None) -> Path:
    file_path = workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME
    save_json_file(file_path, state)
    return file_path


def get_state_entry(state: dict | None, key: str) -> dict:
    if not isinstance(state, dict):
        return {}
    value = state.get(key, {})
    return value if isinstance(value, dict) else {}


def update_state_entry(category: str, record: dict, workspace: str | None = None) -> dict:
    state = load_workspace_state(workspace)
    state[category] = {**record, "timestamp": record.get("timestamp") or now_iso()}
    file_path = save_workspace_state(state, workspace)
    return {
        "workspace": str(workspace_root(workspace)),
        "file": str(file_path),
        "updated_keys": [category],
        category: state[category],
    }


def _first_resolved(mapping: dict, keys: list[str]) -> tuple[Any, str | None]:
    for key in keys:
        value = mapping.get(key)
        if not is_missing(value):
            return value, key
    return None, None


def resolve_param(
    name: str,
    cli_value: Any,
    *,
    config: dict | None = None,
    config_keys: list[str] | None = None,
    state_record: dict | None = None,
    state_keys: list[str] | None = None,
    required: bool = False,
    normalize_as_path: bool = False,
) -> tuple[Any, str]:
    if not is_missing(cli_value):
        value = cli_value
        source = "cli"
    else:
        value = None
        source = ""
        if config and config_keys:
            value, config_key = _first_resolved(config, config_keys)
            if not is_missing(value):
                source = f"config:{config_key}"
        if is_missing(value) and state_record and state_keys:
            value, state_key = _first_resolved(state_record, state_keys)
            if not is_missing(value):
                source = f"state:{state_key}"
    if normalize_as_path and not is_missing(value):
        value = normalize_path(str(value))
    if required and is_missing(value):
        raise ValueError(f"缺少必要参数: {name}")
    return value, source


def compact_dict(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if value not in (None, "", [], {})}


def build_artifacts(**paths: str) -> dict:
    return {key: normalize_path(str(value)) for key, value in paths.items() if not is_missing(value)}


def make_result(
    *,
    status: str,
    action: str,
    summary: str,
    details: dict | None = None,
    context: dict | None = None,
    artifacts: dict | None = None,
    metrics: dict | None = None,
    state: dict | None = None,
    next_actions: list[str] | None = None,
    timing: dict | None = None,
    error: dict | None = None,
) -> dict:
    result = {"status": status, "action": action, "summary": summary, "details": compact_dict(details)}
    optional = {
        "context": compact_dict(context),
        "artifacts": compact_dict(artifacts),
        "metrics": compact_dict(metrics),
        "state": compact_dict(state),
        "timing": compact_dict(timing),
    }
    for key, value in optional.items():
        if value:
            result[key] = value
    if next_actions:
        result["next_actions"] = [item for item in next_actions if item]
    if error:
        result["error"] = compact_dict(error)
    return result


def make_timing(started_at: str, elapsed_ms: int | float) -> dict:
    return {"started_at": started_at, "finished_at": now_iso(), "elapsed_ms": int(elapsed_ms)}


def parameter_context(*, provider: str, workspace: str | None = None, parameter_sources: dict | None = None, config_path: str | None = None) -> dict:
    context = {"provider": provider, "workspace": str(workspace_root(workspace))}
    if parameter_sources:
        context["parameter_sources"] = compact_dict(parameter_sources)
    if not is_missing(config_path):
        context["config_path"] = normalize_path(str(config_path))
    return context
