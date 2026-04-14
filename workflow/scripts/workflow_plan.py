"""workflow 规划：发现工程、候选后端和状态。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from workflow_runtime import (  # noqa: E402
    get_state_entry,
    load_effective_project_config,
    load_workspace_state,
    make_result,
    make_timing,
    now_iso,
    output_json,
    parameter_context,
    workspace_root,
)


def discover_projects(root: Path) -> dict:
    keil_projects = sorted(str(path.resolve()) for path in root.rglob("*.uvprojx"))
    gcc_projects = sorted(str(path.parent.resolve()) for path in root.rglob("CMakePresets.json"))
    return {
        "keil_projects": keil_projects,
        "gcc_projects": gcc_projects,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="workflow plan")
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--config", default=None, help="workflow config.json 路径（已废弃，仅保留兼容性）")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    started_at = now_iso()
    started_ts = __import__("time").time()
    workspace = workspace_root(args.workspace)
    try:
        full_config, config_path = load_effective_project_config(str(workspace), args.config)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        result = make_result(
            status="error",
            action="plan",
            summary=str(exc),
            context=parameter_context(provider="workflow", workspace=str(workspace)),
            error={"code": "invalid_config", "message": str(exc)},
            timing=make_timing(started_at, (__import__("time").time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    workflow_config = full_config.get("workflow", {})
    state = load_workspace_state(str(workspace))
    discovery = discover_projects(workspace)

    build_candidates = []
    if discovery["keil_projects"]:
        build_candidates.append("keil")
    if discovery["gcc_projects"]:
        build_candidates.append("gcc")

    # 从配置中读取 preferred 设置
    preferred = {
        "build": workflow_config.get("preferred_build", "auto"),
        "flash": workflow_config.get("preferred_flash", "auto"),
        "debug": workflow_config.get("preferred_debug", "auto"),
        "observe": workflow_config.get("preferred_observe", "auto"),
    }

    result = make_result(
        status="ok",
        action="plan",
        summary="workflow 规划已生成",
        details={
            "workspace": str(workspace),
            "build_candidates": build_candidates,
            "flash_candidates": ["jlink", "openocd"],
            "debug_candidates": ["jlink", "openocd"],
            "observe_candidates": ["jlink:rtt", "openocd:semihosting", "jlink:swo", "openocd:itm"],
            "preferred": preferred,
        },
        context=parameter_context(provider="workflow", workspace=str(workspace), config_path=config_path),
        metrics={
            "keil_projects": len(discovery["keil_projects"]),
            "gcc_projects": len(discovery["gcc_projects"]),
        },
        state={
            "last_build": get_state_entry(state, "last_build"),
            "last_flash": get_state_entry(state, "last_flash"),
            "last_debug": get_state_entry(state, "last_debug"),
            "last_observe": get_state_entry(state, "last_observe"),
        },
        next_actions=["若存在多个候选工程，workflow run 会返回候选列表而不会自动猜测"],
        timing=make_timing(started_at, (__import__("time").time() - started_ts) * 1000),
    )
    result["details"].update(discovery)

    if args.as_json:
        output_json(result)
    else:
        print(result["summary"])
        print(f"workspace: {workspace}")
        print(f"keil: {len(discovery['keil_projects'])}, gcc: {len(discovery['gcc_projects'])}")
        print(f"preferred: build={preferred['build']}, flash={preferred['flash']}, debug={preferred['debug']}, observe={preferred['observe']}")


if __name__ == "__main__":
    main()
