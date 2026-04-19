"""jlink skill 私有运行时工具。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


STATE_DIR_NAME = ".embeddedskills"
STATE_FILE_NAME = "state.json"
PROJECT_CONFIG_FILE_NAME = "config.json"
SKILL_NAME = "jlink"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_config_path(script_file: str) -> Path:
    return Path(script_file).resolve().parents[1] / "config.json"


def load_local_config(script_file: str = "") -> dict:
    """加载 skill/config.json（环境级配置）"""
    if script_file:
        config_path = default_config_path(script_file)
    else:
        # 尝试从调用栈推断路径
        import inspect

        frame = inspect.currentframe()
        if frame and frame.f_back:
            caller_file = frame.f_back.f_globals.get("__file__", "")
            if caller_file:
                config_path = default_config_path(caller_file)
            else:
                config_path = Path(__file__).resolve().parents[1] / "config.json"
        else:
            config_path = Path(__file__).resolve().parents[1] / "config.json"
    return load_json_file(config_path)


def save_local_config(data: dict, script_file: str = "") -> None:
    """保存环境级配置到 skill/config.json"""
    if script_file:
        config_path = default_config_path(script_file)
    else:
        config_path = Path(__file__).resolve().parents[1] / "config.json"
    save_json_file(config_path, data)


def load_project_config(workspace: str | None = None) -> dict:
    """从 workspace/.embeddedskills/config.json 读取本 skill 的工程级配置
    参数: workspace - 工作区路径，None 时使用 cwd
    返回: 该 skill 对应的配置字典
    """
    ws_root = workspace_root(workspace)
    project_config_path = ws_root / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME
    full_config = load_json_file(project_config_path)
    return full_config.get(SKILL_NAME, {})


def save_project_config(workspace: str | None = None, values: dict | None = None) -> None:
    """写回工程级配置到 workspace/.embeddedskills/config.json
    - 只更新本 skill 的配置部分，不覆盖其他 skill 的配置
    - 目录不存在时自动创建 .embeddedskills/
    - jlink_runtime 中 skill_name 硬编码为 "jlink"
    """
    if values is None:
        values = {}
    ws_root = workspace_root(workspace)
    project_config_path = ws_root / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME

    # 读取现有配置（如果存在）
    full_config = load_json_file(project_config_path)
    if not isinstance(full_config, dict):
        full_config = {}

    # 只更新本 skill 的配置部分
    full_config[SKILL_NAME] = {**(full_config.get(SKILL_NAME) or {}), **values}

    save_json_file(project_config_path, full_config)


def output_json(data: dict, *, indent: int = 2) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=indent), flush=True)


def is_missing(value: Any) -> bool:
    return value is None or value == ""


def normalize_path(value: str | None) -> str:
    if is_missing(value):
        return ""
    return str(Path(str(value)).expanduser().resolve())


def normalize_path_with_base(value: str | None, base: str | Path | None = None) -> str:
    if is_missing(value):
        return ""
    path = Path(str(value)).expanduser()
    if base and not path.is_absolute():
        path = Path(base) / path
    return str(path.resolve())


def _serialize_state_value(value: Any, workspace: Path) -> Any:
    if isinstance(value, dict):
        return {key: _serialize_state_value(item, workspace) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_state_value(item, workspace) for item in value]
    if not isinstance(value, str) or "://" in value:
        return value

    path = Path(value).expanduser()
    if not path.is_absolute():
        return value
    try:
        return Path(os.path.relpath(path.resolve(), workspace)).as_posix()
    except ValueError:
        return value


def hidden_subprocess_kwargs(*, new_process_group: bool = False) -> dict:
    if sys.platform != "win32":
        return {}

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if new_process_group:
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return {
        "creationflags": creationflags,
        "startupinfo": startupinfo,
    }


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


def workspace_root(workspace: str | None = None) -> Path:
    if not is_missing(workspace):
        return Path(str(workspace)).expanduser().resolve()
    return Path.cwd().resolve()


def load_workspace_state(workspace: str | None = None) -> dict:
    return load_json_file(workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME)


def save_workspace_state(state: dict, workspace: str | None = None) -> Path:
    ws_root = workspace_root(workspace)
    file_path = ws_root / STATE_DIR_NAME / STATE_FILE_NAME
    save_json_file(file_path, _serialize_state_value(state, ws_root))
    return file_path


def get_state_entry(state: dict | None, key: str) -> dict:
    if not isinstance(state, dict):
        return {}
    value = state.get(key, {})
    return value if isinstance(value, dict) else {}


def update_state_entry(category: str, record: dict, workspace: str | None = None) -> dict:
    ws_root = workspace_root(workspace)
    state = load_workspace_state(workspace)
    state[category] = _serialize_state_value({**record, "timestamp": record.get("timestamp") or now_iso()}, ws_root)
    file_path = save_workspace_state(state, workspace)
    return {
        "workspace": str(ws_root),
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


def normalize_command_value(value: str | None) -> str:
    if is_missing(value):
        return ""
    candidate = str(value).strip()
    expanded = Path(candidate).expanduser()
    if expanded.exists():
        return str(expanded.resolve())
    resolved = shutil.which(candidate)
    if resolved:
        return normalize_path(resolved)
    return candidate


def resolve_path_candidate(candidates: list[str] | tuple[str, ...] | None) -> tuple[str, str]:
    for candidate in candidates or []:
        if is_missing(candidate):
            continue
        resolved = shutil.which(str(candidate))
        if resolved:
            return normalize_path(resolved), f"path:{candidate}"
    return "", ""


def resolve_tool_param(
    name: str,
    cli_value: Any,
    *,
    local_config: dict | None = None,
    local_keys: list[str] | None = None,
    path_candidates: list[str] | tuple[str, ...] | None = None,
    default: Any = None,
    required: bool = False,
) -> tuple[Any, str]:
    if not is_missing(cli_value):
        value = normalize_command_value(str(cli_value))
        source = "cli"
    else:
        value = None
        source = ""
        if local_config and local_keys:
            value, local_key = _first_resolved(local_config, local_keys)
            if not is_missing(value):
                value = normalize_command_value(str(value))
                source = f"config:{local_key}"
        if is_missing(value):
            value, source = resolve_path_candidate(path_candidates)
        if is_missing(value) and not is_missing(default):
            value = default
            source = f"default:{default}" if isinstance(default, str) else "default"
    if required and is_missing(value):
        raise ValueError(f"缺少必要参数: {name}")
    return value, source


def resolve_project_param(
    name: str,
    cli_value: Any,
    *,
    project_config: dict | None = None,
    project_keys: list[str] | None = None,
    state_record: dict | None = None,
    state_keys: list[str] | None = None,
    default: Any = None,
    required: bool = False,
    normalize_as_path: bool = False,
    workspace: str | None = None,
) -> tuple[Any, str]:
    if not is_missing(cli_value):
        value = cli_value
        source = "cli"
    else:
        value = None
        source = ""
        if project_config and project_keys:
            value, project_key = _first_resolved(project_config, project_keys)
            if not is_missing(value):
                source = f"project_config:{project_key}"
        if is_missing(value) and state_record and state_keys:
            value, state_key = _first_resolved(state_record, state_keys)
            if not is_missing(value):
                source = f"state:{state_key}"
        if is_missing(value) and not is_missing(default):
            value = default
            source = "default"
    if normalize_as_path and not is_missing(value):
        value = normalize_path_with_base(str(value), workspace_root(workspace))
    if required and is_missing(value):
        raise ValueError(f"缺少必要参数: {name}")
    return value, source


def resolve_runtime_param(
    name: str,
    cli_value: Any,
    *,
    project_config: dict | None = None,
    project_keys: list[str] | None = None,
    local_config: dict | None = None,
    local_keys: list[str] | None = None,
    state_record: dict | None = None,
    state_keys: list[str] | None = None,
    default: Any = None,
    required: bool = False,
    normalize_as_path: bool = False,
    workspace: str | None = None,
) -> tuple[Any, str]:
    if not is_missing(cli_value):
        value = cli_value
        source = "cli"
    else:
        value = None
        source = ""
        if project_config and project_keys:
            value, project_key = _first_resolved(project_config, project_keys)
            if not is_missing(value):
                source = f"project_config:{project_key}"
        if is_missing(value) and local_config and local_keys:
            value, local_key = _first_resolved(local_config, local_keys)
            if not is_missing(value):
                source = f"config:{local_key}"
        if is_missing(value) and state_record and state_keys:
            value, state_key = _first_resolved(state_record, state_keys)
            if not is_missing(value):
                source = f"state:{state_key}"
        if is_missing(value) and not is_missing(default):
            value = default
            source = "default"
    if normalize_as_path and not is_missing(value):
        value = normalize_path_with_base(str(value), workspace_root(workspace))
    if required and is_missing(value):
        raise ValueError(f"缺少必要参数: {name}")
    return value, source


def resolve_artifact_param(
    name: str,
    cli_value: Any,
    *,
    project_config: dict | None = None,
    project_keys: list[str] | None = None,
    state_record: dict | None = None,
    state_keys: list[str] | None = None,
    default: Any = None,
    required: bool = False,
    normalize_as_path: bool = False,
    workspace: str | None = None,
) -> tuple[Any, str]:
    return resolve_project_param(
        name,
        cli_value,
        project_config=project_config,
        project_keys=project_keys,
        state_record=state_record,
        state_keys=state_keys,
        default=default,
        required=required,
        normalize_as_path=normalize_as_path,
        workspace=workspace,
    )


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
    workspace: str | None = None,
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
        value = normalize_path_with_base(str(value), workspace_root(workspace))
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


def emit_stream_record(*, source: str, channel_type: str, text: str, as_json: bool, stream_type: str = "text", channel: int | None = None, extra: dict | None = None) -> None:
    if as_json:
        record = {
            "timestamp": now_iso(),
            "source": source,
            "channel_type": channel_type,
            "stream_type": stream_type,
            "text": text.rstrip("\r\n"),
        }
        if channel is not None:
            record["channel"] = channel
        if extra:
            record.update(compact_dict(extra))
        print(json.dumps(record, ensure_ascii=False), flush=True)
    else:
        print(text, end="" if text.endswith(("\n", "\r")) else "\n", flush=True)
