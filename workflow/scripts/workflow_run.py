"""workflow 薄编排执行层。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
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
    save_project_config,
    update_state_entry,
    workspace_root,
)


PYTHON_EXE = sys.executable


def _with_backend(result: dict, backend: str) -> dict:
    details = dict(result.get("details") or {})
    details["backend"] = backend
    result["details"] = details
    return result


def discover_projects(root: Path) -> dict:
    return {
        "keil": sorted(str(path.resolve()) for path in root.rglob("*.uvprojx")),
        "gcc": sorted(str(path.parent.resolve()) for path in root.rglob("CMakePresets.json")),
    }


def _single_or_error(items: list[str], label: str) -> tuple[str | None, dict | None]:
    if len(items) == 1:
        return items[0], None
    if len(items) > 1:
        return None, {"code": "multiple_candidates", "message": f"发现多个{label}，请在配置或命令中显式指定", "candidates": items}
    return None, {"code": "not_found", "message": f"未发现可用的{label}", "candidates": []}


def run_json(cmd: list[str], workdir: Path) -> dict:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(workdir), encoding="utf-8", errors="replace")
    payload = (proc.stdout or proc.stderr).strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "action": "subprocess",
            "error": {"code": "invalid_json", "message": payload[-500:] or "子进程未返回 JSON"},
        }


def select_build_backend(workflow_config: dict, discovery: dict, explicit: str | None) -> tuple[str | None, dict | None]:
    backend = explicit or workflow_config.get("preferred_build") or "auto"
    if backend != "auto":
        return backend, None
    candidates = [name for name in ("keil", "gcc") if discovery[name]]
    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        return None, {"code": "multiple_build_backends", "message": "同时发现 Keil 和 GCC 工程，请显式指定 build backend", "candidates": candidates}
    return None, {"code": "no_build_backend", "message": "未发现可构建工程", "candidates": []}


def build_project(workspace: Path, full_config: dict, discovery: dict, backend: str | None) -> dict:
    workflow_config = full_config.get("workflow", {})
    selected, error = select_build_backend(workflow_config, discovery, backend)
    if error:
        return {"status": "error", "action": "build", "error": error}

    if selected == "keil":
        keil_config = full_config.get("keil", {})
        project = keil_config.get("project")
        if not project:
            project, error = _single_or_error(discovery["keil"], "Keil 工程")
            if error:
                return {"status": "error", "action": "build", "error": error}
        cmd = [
            PYTHON_EXE,
            str(ROOT_DIR / "keil" / "scripts" / "keil_build.py"),
            "build",
            "--workspace",
            str(workspace),
            "--project",
            project,
            "--json",
        ]
        target = keil_config.get("target")
        uv4_exe = keil_config.get("uv4_exe")
        if target:
            cmd.extend(["--target", target])
        if uv4_exe:
            cmd.extend(["--uv4", uv4_exe])
        return _with_backend(run_json(cmd, workspace), selected)

    gcc_config = full_config.get("gcc", {})
    project = gcc_config.get("project")
    if not project:
        project, error = _single_or_error(discovery["gcc"], "GCC 工程")
        if error:
            return {"status": "error", "action": "build", "error": error}
    preset = gcc_config.get("preset")
    if not preset:
        return {"status": "error", "action": "build", "error": {"code": "missing_preset", "message": "需要在 .embeddedskills/config.json 的 gcc 段配置 preset"}}
    cmd = [
        PYTHON_EXE,
        str(ROOT_DIR / "gcc" / "scripts" / "gcc_build.py"),
        "build",
        "--workspace",
        str(workspace),
        "--project",
        project,
        "--preset",
        preset,
        "--json",
    ]
    cmake_exe = gcc_config.get("cmake_exe")
    if cmake_exe:
        cmd.extend(["--cmake", cmake_exe])
    return _with_backend(run_json(cmd, workspace), selected)


def flash_project(workspace: Path, full_config: dict, state: dict, explicit: str | None) -> dict:
    workflow_config = full_config.get("workflow", {})
    backend = explicit or workflow_config.get("preferred_flash") or "auto"
    last_build = get_state_entry(state, "last_build")
    artifacts = last_build.get("artifacts", {})
    flash_file = last_build.get("flash_file") or artifacts.get("flash_file")
    if not flash_file:
        return {"status": "error", "action": "flash", "error": {"code": "missing_last_build", "message": "未找到 last_build.flash_file，请先执行 workflow build"}}

    if backend in ("auto", "openocd"):
        openocd_cfg = full_config.get("openocd", {})
        cmd = [
            PYTHON_EXE,
            str(ROOT_DIR / "openocd" / "scripts" / "openocd_run.py"),
            "flash",
            "--workspace",
            str(workspace),
            "--file",
            flash_file,
            "--json",
        ]
        if openocd_cfg.get("board"):
            cmd.extend(["--board", openocd_cfg["board"]])
        if openocd_cfg.get("interface"):
            cmd.extend(["--interface", openocd_cfg["interface"]])
        if openocd_cfg.get("target"):
            cmd.extend(["--target", openocd_cfg["target"]])
        return _with_backend(run_json(cmd, workspace), "openocd")

    jlink_cfg = full_config.get("jlink", {})
    if not jlink_cfg.get("device"):
        return {"status": "error", "action": "flash", "error": {"code": "missing_device", "message": "使用 jlink flash 时需要在 .embeddedskills/config.json 的 jlink 段提供 device"}}
    cmd = [
        PYTHON_EXE,
        str(ROOT_DIR / "jlink" / "scripts" / "jlink_exec.py"),
        "flash",
        "--file",
        flash_file,
        "--device",
        jlink_cfg["device"],
        "--json",
    ]
    if jlink_cfg.get("interface"):
        cmd.extend(["--interface", jlink_cfg["interface"]])
    if jlink_cfg.get("speed"):
        cmd.extend(["--speed", str(jlink_cfg["speed"])])
    return _with_backend(run_json(cmd, workspace), "jlink")


def debug_project(workspace: Path, full_config: dict, state: dict, explicit: str | None) -> dict:
    workflow_config = full_config.get("workflow", {})
    backend = explicit or workflow_config.get("preferred_debug") or "auto"
    last_build = get_state_entry(state, "last_build")
    artifacts = last_build.get("artifacts", {})
    debug_file = last_build.get("debug_file") or artifacts.get("debug_file")
    if not debug_file:
        return {"status": "error", "action": "build-debug", "error": {"code": "missing_last_build", "message": "未找到 last_build.debug_file，请先执行 workflow build"}}

    if backend in ("auto", "openocd"):
        openocd_cfg = full_config.get("openocd", {})
        cmd = [
            PYTHON_EXE,
            str(ROOT_DIR / "openocd" / "scripts" / "openocd_gdb.py"),
            "crash-report",
            "--workspace",
            str(workspace),
            "--elf",
            debug_file,
            "--json",
        ]
        if openocd_cfg.get("board"):
            cmd.extend(["--board", openocd_cfg["board"]])
        if openocd_cfg.get("interface"):
            cmd.extend(["--interface", openocd_cfg["interface"]])
        if openocd_cfg.get("target"):
            cmd.extend(["--target", openocd_cfg["target"]])
        if openocd_cfg.get("gdb_exe"):
            cmd.extend(["--gdb-exe", openocd_cfg["gdb_exe"]])
        return _with_backend(run_json(cmd, workspace), "openocd")

    jlink_cfg = full_config.get("jlink", {})
    if not jlink_cfg.get("device"):
        return {"status": "error", "action": "build-debug", "error": {"code": "missing_device", "message": "使用 jlink gdb 时需要在 .embeddedskills/config.json 的 jlink 段提供 device"}}
    cmd = [
        PYTHON_EXE,
        str(ROOT_DIR / "jlink" / "scripts" / "jlink_gdb.py"),
        "crash-report",
        "--workspace",
        str(workspace),
        "--elf",
        debug_file,
        "--device",
        jlink_cfg["device"],
        "--json",
    ]
    if jlink_cfg.get("interface"):
        cmd.extend(["--interface", jlink_cfg["interface"]])
    if jlink_cfg.get("speed"):
        cmd.extend(["--speed", str(jlink_cfg["speed"])])
    return _with_backend(run_json(cmd, workspace), "jlink")


def observe_project(workspace: Path, full_config: dict, explicit: str | None) -> dict:
    workflow_config = full_config.get("workflow", {})
    backend = explicit or workflow_config.get("preferred_observe") or "auto"
    if backend in ("auto", "openocd"):
        openocd_cfg = full_config.get("openocd", {})
        cmd = [
            PYTHON_EXE,
            str(ROOT_DIR / "openocd" / "scripts" / "openocd_semihosting.py"),
            "--workspace",
            str(workspace),
            "--json",
        ]
        if openocd_cfg.get("board"):
            cmd.extend(["--board", openocd_cfg["board"]])
        if openocd_cfg.get("interface"):
            cmd.extend(["--interface", openocd_cfg["interface"]])
        if openocd_cfg.get("target"):
            cmd.extend(["--target", openocd_cfg["target"]])
        return {"status": "ok", "action": "observe", "summary": "已生成 openocd semihosting 观察命令", "details": {"command": cmd, "backend": "openocd"}}

    jlink_cfg = full_config.get("jlink", {})
    if not jlink_cfg.get("device"):
        return {"status": "error", "action": "observe", "error": {"code": "missing_device", "message": "使用 jlink 观测时需要在 .embeddedskills/config.json 的 jlink 段提供 device"}}
    cmd = [
        PYTHON_EXE,
        str(ROOT_DIR / "jlink" / "scripts" / "jlink_rtt.py"),
        "--workspace",
        str(workspace),
        "--device",
        jlink_cfg["device"],
        "--json",
    ]
    return {"status": "ok", "action": "observe", "summary": "已生成 jlink RTT 观察命令", "details": {"command": cmd, "backend": "jlink"}}


def diagnose(workspace: Path, full_config: dict, discovery: dict, state: dict) -> dict:
    workflow_config = full_config.get("workflow", {})
    hints = []
    if not discovery["keil"] and not discovery["gcc"]:
        hints.append("当前 workspace 未发现 Keil/GCC 工程")
    if not get_state_entry(state, "last_build"):
        hints.append("尚未生成 last_build，后续 flash/debug 无法自动串联")
    if workflow_config.get("preferred_build") == "auto" and discovery["keil"] and discovery["gcc"]:
        hints.append("同时存在 Keil 与 GCC 工程，建议在 .embeddedskills/config.json 的 workflow 段固定 preferred_build")
    return {
        "status": "ok",
        "action": "diagnose",
        "summary": "workflow 诊断完成",
        "details": {
            "workspace": str(workspace),
            "discovery": discovery,
            "state": {
                "last_build": get_state_entry(state, "last_build"),
                "last_flash": get_state_entry(state, "last_flash"),
                "last_debug": get_state_entry(state, "last_debug"),
                "last_observe": get_state_entry(state, "last_observe"),
            },
            "hints": hints,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="workflow run")
    parser.add_argument("action", choices=["plan", "build", "build-flash", "build-debug", "observe", "diagnose"])
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--config", default=None, help="workflow config.json 路径（已废弃，仅保留兼容性）")
    parser.add_argument("--build-backend", choices=["auto", "keil", "gcc"], default=None)
    parser.add_argument("--flash-backend", choices=["auto", "jlink", "openocd"], default=None)
    parser.add_argument("--debug-backend", choices=["auto", "jlink", "openocd"], default=None)
    parser.add_argument("--observe-backend", choices=["auto", "jlink", "openocd"], default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    started_at = now_iso()
    started_ts = time.time()
    workspace = workspace_root(args.workspace)
    try:
        full_config, config_path = load_effective_project_config(str(workspace), args.config)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        wrapped = make_result(
            status="error",
            action=args.action,
            summary=str(exc),
            context=parameter_context(provider="workflow", workspace=str(workspace)),
            error={"code": "invalid_config", "message": str(exc)},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(wrapped)
        else:
            print(f"[workflow {args.action}] {wrapped['summary']}")
        sys.exit(1)

    workflow_config = full_config.get("workflow", {})
    state = load_workspace_state(str(workspace))
    discovery = discover_projects(workspace)

    # 用于追踪实际使用的后端，成功后将写回配置
    used_backends = {}

    if args.action == "plan":
        cmd = [PYTHON_EXE, str(ROOT_DIR / "workflow" / "scripts" / "workflow_plan.py"), "--workspace", str(workspace), "--json"]
        if config_path:
            cmd.extend(["--config", config_path])
        result = run_json(cmd, workspace)
    elif args.action == "build":
        result = build_project(workspace, full_config, discovery, args.build_backend)
        if result.get("status") == "ok" and result.get("details", {}).get("backend"):
            used_backends["preferred_build"] = result["details"]["backend"]
    elif args.action == "build-flash":
        build_result = build_project(workspace, full_config, discovery, args.build_backend)
        if build_result.get("status") == "error":
            result = build_result
        else:
            if build_result.get("details", {}).get("backend"):
                used_backends["preferred_build"] = build_result["details"]["backend"]
            state = load_workspace_state(str(workspace))
            flash_result = flash_project(workspace, full_config, state, args.flash_backend)
            if flash_result.get("status") == "ok" and flash_result.get("details", {}).get("backend"):
                used_backends["preferred_flash"] = flash_result["details"]["backend"]
            result = {
                "status": flash_result.get("status", "error"),
                "action": "build-flash",
                "summary": "build-flash 完成" if flash_result.get("status") == "ok" else flash_result.get("error", {}).get("message", "build-flash 失败"),
                "details": {"build": build_result, "flash": flash_result},
            }
    elif args.action == "build-debug":
        build_result = build_project(workspace, full_config, discovery, args.build_backend)
        if build_result.get("status") == "error":
            result = build_result
        else:
            if build_result.get("details", {}).get("backend"):
                used_backends["preferred_build"] = build_result["details"]["backend"]
            state = load_workspace_state(str(workspace))
            debug_result = debug_project(workspace, full_config, state, args.debug_backend)
            if debug_result.get("status") == "ok" and debug_result.get("details", {}).get("backend"):
                used_backends["preferred_debug"] = debug_result["details"]["backend"]
            result = {
                "status": debug_result.get("status", "error"),
                "action": "build-debug",
                "summary": "build-debug 完成" if debug_result.get("status") == "ok" else debug_result.get("error", {}).get("message", "build-debug 失败"),
                "details": {"build": build_result, "debug": debug_result},
            }
    elif args.action == "observe":
        result = observe_project(workspace, full_config, args.observe_backend)
        if result.get("status") == "ok" and result.get("details", {}).get("backend"):
            used_backends["preferred_observe"] = result["details"]["backend"]
    else:
        result = diagnose(workspace, full_config, discovery, state)

    # 将确认过的 preferred 值写回 .embeddedskills/config.json
    if used_backends:
        save_project_config(str(workspace), used_backends)

    # 更新运行状态到 state.json
    if result.get("status") == "ok" and args.action in ("build", "build-flash", "build-debug", "observe"):
        state_record = {
            "action": args.action,
            "timestamp": now_iso(),
        }
        if result.get("details"):
            state_record["details"] = result["details"]
        update_state_entry(f"last_{args.action.replace('-', '_')}", state_record, str(workspace))

    wrapped = make_result(
        status=result.get("status", "error"),
        action=args.action,
        summary=result.get("summary", "workflow 执行完成"),
        details=result.get("details", {}),
        context=parameter_context(provider="workflow", workspace=str(workspace), config_path=config_path),
        error=result.get("error"),
        timing=make_timing(started_at, (time.time() - started_ts) * 1000),
    )

    if args.as_json:
        output_json(wrapped)
    else:
        print(f"[workflow {args.action}] {wrapped['summary']}")
        if wrapped.get("error"):
            sys.exit(1)


if __name__ == "__main__":
    main()
