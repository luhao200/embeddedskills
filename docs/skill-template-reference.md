# Embedded Skill 参考模版

## 1. 这些 skill 的共同特点

当前仓库里的 skill，虽然面向的工具不同，但实现风格基本收敛到了同一套模型：

### 1.1 三层配置 + PATH

共同读取这几层运行环境：

1. CLI 显式参数
2. `skill/config.json`
3. `<workspace>/.embeddedskills/config.json`
4. `<workspace>/.embeddedskills/state.json`
5. 系统 `PATH`
6. 默认值

其中：

- 工具路径优先走 `CLI > skill/config.json > PATH > 默认命令名`
- 工程参数优先走 `CLI > .embeddedskills/config.json > state.json > 默认值/报错`
- 产物路径优先走 `CLI > .embeddedskills/config.json > state.json > 默认值/报错`

### 1.2 统一 runtime 层

大多数 skill 都有一个 `*_runtime.py`，负责：

- 读写环境级配置
- 读写工程级配置
- 读写状态文件
- 标准化路径
- 统一结果输出
- 参数来源跟踪
- 参数解析 helper

典型公共函数：

- `load_local_config`
- `load_project_config`
- `save_project_config`
- `load_workspace_state`
- `update_state_entry`
- `resolve_tool_param`
- `resolve_project_param`
- `resolve_runtime_param`
- `resolve_artifact_param`
- `make_result`
- `make_timing`
- `parameter_context`

### 1.3 统一结果格式

几乎所有脚本都返回结构化 JSON，核心字段一致：

```json
{
  "status": "ok",
  "action": "build",
  "summary": "执行成功",
  "details": {},
  "context": {},
  "artifacts": {},
  "metrics": {},
  "state": {},
  "timing": {}
}
```

流式输出类命令额外会用 JSON Lines，并带上：

- `source`
- `channel_type`
- `stream_type`
- `timestamp`

### 1.4 工程配置与状态分离

共同约束：

- `.embeddedskills/config.json` 存“长期可复用的工程默认值”
- `.embeddedskills/state.json` 存“最近一次成功执行的运行记录”
- 成功后通常会：
  - 把确认过的工程参数写回 `.embeddedskills/config.json`
  - 把最近一次执行信息写回 `.embeddedskills/state.json`

### 1.5 入口脚本职责清晰

每个入口脚本基本都做同样几步：

1. 解析 CLI 参数
2. 加载本地配置、工程配置、状态
3. 用 runtime helper 解析参数
4. 校验必填项
5. 调用底层工具
6. 解析输出
7. 写回 config/state
8. 返回标准 JSON

### 1.6 文档结构一致

每个 skill 通常有：

- `SKILL.md`
- `README.md`
- `config.example.json`
- `scripts/`
- `references/`
- `templates/`（如需要）

---

## 2. 推荐目录结构

新增 skill 时，建议直接使用这个目录骨架：

```text
your-skill/
├── SKILL.md
├── README.md
├── config.example.json
├── scripts/
│   ├── your_skill_runtime.py
│   ├── your_skill_exec.py
│   ├── your_skill_scan.py
│   └── your_skill_observe.py
├── references/
│   └── common_devices.json
└── templates/
    └── sample.txt
```

说明：

- `runtime.py` 必须有
- `exec.py` 负责主命令入口
- `scan.py` 负责发现/扫描
- 其他脚本按场景拆分，不要把所有逻辑塞进一个文件

---

## 3. runtime.py 模版

下面是推荐骨架，按当前仓库主流实现收敛后的最小版本：

```python
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


STATE_DIR_NAME = ".embeddedskills"
STATE_FILE_NAME = "state.json"
PROJECT_CONFIG_FILE_NAME = "config.json"
SKILL_NAME = "your-skill"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def is_missing(value: Any) -> bool:
    return value is None or value == ""


def default_config_path(script_file: str) -> Path:
    return Path(script_file).resolve().parents[1] / "config.json"


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


def normalize_path(value: str | None) -> str:
    if is_missing(value):
        return ""
    return str(Path(str(value)).expanduser().resolve())


def load_local_config(script_file: str | None = None) -> dict:
    if not script_file:
        return {}
    return load_json_file(default_config_path(script_file))


def load_project_config(workspace: str | None = None) -> dict:
    ws = workspace_root(workspace)
    config_file = ws / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME
    data = load_json_file(config_file)
    return data.get(SKILL_NAME, {})


def save_project_config(workspace: str | None = None, values: dict | None = None) -> None:
    if values is None:
        values = {}
    ws = workspace_root(workspace)
    config_file = ws / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME
    data = load_json_file(config_file)
    data[SKILL_NAME] = {**(data.get(SKILL_NAME, {})), **values}
    save_json_file(config_file, data)


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


def resolve_tool_param(name: str, cli_value: Any, *, local_config: dict | None = None, local_keys: list[str] | None = None, path_candidates: list[str] | tuple[str, ...] | None = None, default: Any = None, required: bool = False) -> tuple[Any, str]:
    if not is_missing(cli_value):
        value = normalize_command_value(str(cli_value))
        source = "cli"
    else:
        value = None
        source = ""
        if local_config and local_keys:
            value, key = _first_resolved(local_config, local_keys)
            if not is_missing(value):
                value = normalize_command_value(str(value))
                source = f"config:{key}"
        if is_missing(value):
            value, source = resolve_path_candidate(path_candidates)
        if is_missing(value) and not is_missing(default):
            value = default
            source = f"default:{default}" if isinstance(default, str) else "default"
    if required and is_missing(value):
        raise ValueError(f"缺少必要参数: {name}")
    return value, source


def resolve_project_param(name: str, cli_value: Any, *, project_config: dict | None = None, project_keys: list[str] | None = None, state_record: dict | None = None, state_keys: list[str] | None = None, default: Any = None, required: bool = False, normalize_as_path: bool = False) -> tuple[Any, str]:
    if not is_missing(cli_value):
        value = cli_value
        source = "cli"
    else:
        value = None
        source = ""
        if project_config and project_keys:
            value, key = _first_resolved(project_config, project_keys)
            if not is_missing(value):
                source = f"project_config:{key}"
        if is_missing(value) and state_record and state_keys:
            value, key = _first_resolved(state_record, state_keys)
            if not is_missing(value):
                source = f"state:{key}"
        if is_missing(value) and not is_missing(default):
            value = default
            source = "default"
    if normalize_as_path and not is_missing(value):
        value = normalize_path(str(value))
    if required and is_missing(value):
        raise ValueError(f"缺少必要参数: {name}")
    return value, source


def resolve_runtime_param(name: str, cli_value: Any, *, project_config: dict | None = None, project_keys: list[str] | None = None, local_config: dict | None = None, local_keys: list[str] | None = None, state_record: dict | None = None, state_keys: list[str] | None = None, default: Any = None, required: bool = False, normalize_as_path: bool = False) -> tuple[Any, str]:
    if not is_missing(cli_value):
        value = cli_value
        source = "cli"
    else:
        value = None
        source = ""
        if project_config and project_keys:
            value, key = _first_resolved(project_config, project_keys)
            if not is_missing(value):
                source = f"project_config:{key}"
        if is_missing(value) and local_config and local_keys:
            value, key = _first_resolved(local_config, local_keys)
            if not is_missing(value):
                source = f"config:{key}"
        if is_missing(value) and state_record and state_keys:
            value, key = _first_resolved(state_record, state_keys)
            if not is_missing(value):
                source = f"state:{key}"
        if is_missing(value) and not is_missing(default):
            value = default
            source = "default"
    if normalize_as_path and not is_missing(value):
        value = normalize_path(str(value))
    if required and is_missing(value):
        raise ValueError(f"缺少必要参数: {name}")
    return value, source


def resolve_artifact_param(name: str, cli_value: Any, **kwargs) -> tuple[Any, str]:
    return resolve_project_param(name, cli_value, **kwargs)


def compact_dict(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}


def make_result(*, status: str, action: str, summary: str, details: dict | None = None, context: dict | None = None, artifacts: dict | None = None, metrics: dict | None = None, state: dict | None = None, timing: dict | None = None, error: dict | None = None) -> dict:
    result = {
        "status": status,
        "action": action,
        "summary": summary,
        "details": compact_dict(details),
    }
    if context:
        result["context"] = compact_dict(context)
    if artifacts:
        result["artifacts"] = compact_dict(artifacts)
    if metrics:
        result["metrics"] = compact_dict(metrics)
    if state:
        result["state"] = compact_dict(state)
    if timing:
        result["timing"] = compact_dict(timing)
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
```

---

## 4. 主入口脚本模版

建议每个入口脚本按这个顺序组织：

```python
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from your_skill_runtime import (
    default_config_path,
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


def main() -> None:
    parser = argparse.ArgumentParser(description="your-skill 主入口")
    parser.add_argument("action")
    parser.add_argument("--exe", default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    started_at = now_iso()
    started_ts = time.time()
    workspace = workspace_root(args.workspace)
    config_path = normalize_path(args.config or str(default_config_path(__file__)))
    local_config = load_json_file(config_path)
    project_config = load_project_config(str(workspace))
    state = load_workspace_state(str(workspace))

    parameter_sources: dict[str, str] = {}
    try:
        exe, parameter_sources["exe"] = resolve_tool_param(
            "exe",
            args.exe,
            local_config=local_config,
            local_keys=["exe"],
            path_candidates=["tool.exe", "tool"],
            default="tool",
            required=True,
        )
    except ValueError as exc:
        result = make_result(
            status="error",
            action=args.action,
            summary=str(exc),
            context=parameter_context(
                provider="your-skill",
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

    # 这里执行工具调用
    raw_result = {"status": "ok", "summary": "执行成功", "details": {"exe": exe}}

    if raw_result["status"] == "ok":
        save_project_config(str(workspace), {"some_confirmed_param": "value"})
        state_info = update_state_entry("last_action", {"action": args.action}, str(workspace))
        result = make_result(
            status="ok",
            action=args.action,
            summary=raw_result["summary"],
            details=raw_result.get("details"),
            context=parameter_context(
                provider="your-skill",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
                config_path=config_path,
            ),
            state=state_info,
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
    else:
        result = make_result(
            status="error",
            action=args.action,
            summary=raw_result["summary"],
            context=parameter_context(
                provider="your-skill",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
                config_path=config_path,
            ),
            error=raw_result.get("error"),
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )

    if args.as_json:
        output_json(result)
    else:
        print(result["summary"])


if __name__ == "__main__":
    main()
```

---

## 5. config.example.json 模版

### 环境级配置

```json
{
  "exe": "tool.exe",
  "gdb_exe": "arm-none-eabi-gdb.exe",
  "log_dir": "",
  "operation_mode": 1
}
```

建议：

- 这里只放工具路径和本机相关配置
- 不放工程真值参数

### 工程级配置示例

```json
{
  "your-skill": {
    "project": "",
    "device": "",
    "interface": "",
    "speed": "",
    "log_dir": ".embeddedskills/logs/your-skill"
  }
}
```

---

## 6. SKILL.md 模版

```md
---
name: your-skill
description: 一句话描述能力范围
---

# your-skill

## 用途

说明这个 skill 做什么。

## 配置

### 环境级配置（skill/config.json）

用于工具路径和本机参数。

### 工程级配置（.embeddedskills/config.json）

用于工程默认参数。

### 状态文件（.embeddedskills/state.json）

用于保存最近一次成功执行记录。

## 参数解析优先级

- 工具路径：`CLI > skill/config.json > PATH > 默认值`
- 工程参数：`CLI > .embeddedskills/config.json > state.json > 默认值/报错`
- 产物路径：`CLI > .embeddedskills/config.json > state.json > 默认值/报错`

## 子命令

### scan

```powershell
python <skill-dir>/scripts/your_skill_scan.py --json
```

### exec

```powershell
python <skill-dir>/scripts/your_skill_exec.py action --json
```

## 返回格式

所有脚本返回 JSON，基础字段：

- `status`
- `action`
- `summary`
- `details`
- `context`
- `timing`

## 规则

- 缺少关键参数时不自动猜测
- 单一候选可自动发现并写回工程配置
- 成功执行后更新 `.embeddedskills/config.json` 和 `state.json`
```

---

## 7. 新建 skill 的最小检查清单

新增 skill 前，至少确认这几项：

- 是否有独立 `runtime.py`
- 是否区分环境级配置、工程级配置、状态文件
- 是否主动探测系统 `PATH`
- 是否统一返回 JSON
- 是否成功后写回 config/state
- 是否在 `SKILL.md` 中写明参数优先级
- 是否避免把本机绝对路径写进工程配置

---

## 8. 适合直接复用的场景

这份模版最适合以下新 skill：

- 新的烧录后端
- 新的调试器后端
- 新的串口/CAN/网络观测后端
- 新的构建后端
- 任何“CLI 工具包装 + 工程状态管理”型 skill

如果是纯编排层，参考 `workflow`；如果是纯观测层，参考 `serial / net / can`；如果是“构建 + 产物 + 调试”一体型，参考 `gcc / keil / jlink / openocd / probe-rs`。
