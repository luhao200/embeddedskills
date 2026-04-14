"""GCC 嵌入式工程构建：configure / build / rebuild / clean。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from gcc_runtime import (  # noqa: E402
    build_artifacts,
    default_config_path,
    get_state_entry,
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


def _resolve_build_dir(project: Path, preset: str, presets_file: Path) -> Path:
    if presets_file.exists():
        data = json.loads(presets_file.read_text(encoding="utf-8"))
        all_presets = {item["name"]: item for item in data.get("configurePresets", [])}
        preset_item = all_presets.get(preset, {})
        binary_dir = preset_item.get("binaryDir", "")
        if not binary_dir:
            inherits = preset_item.get("inherits")
            if inherits and isinstance(inherits, str):
                binary_dir = all_presets.get(inherits, {}).get("binaryDir", "")
        if binary_dir:
            binary_dir = binary_dir.replace("${sourceDir}", str(project))
            binary_dir = binary_dir.replace("${presetName}", preset)
            return Path(binary_dir)
    return project / "build" / preset


def _resolve_workspace_path(workspace: Path, raw_path: str | None, default: str) -> str:
    value = default if is_missing(raw_path) else str(raw_path)
    path = Path(value)
    return str(path.resolve() if path.is_absolute() else (workspace / path).resolve())


def _resolve_project_path(workspace: Path, raw_path: str | None) -> str:
    if is_missing(raw_path):
        return ""
    path = Path(str(raw_path)).expanduser()
    return str(path.resolve() if path.is_absolute() else (workspace / path).resolve())


def _make_relative_to_workspace(workspace: Path, path: str) -> str:
    """将绝对路径转换为相对于 workspace 的相对路径"""
    try:
        p = Path(path).resolve()
        ws = workspace.resolve()
        rel = p.relative_to(ws)
        return str(rel).replace("\\", "/")
    except ValueError:
        return path


def _find_elf(build_dir: Path, project_name: str) -> str:
    elfs = list(build_dir.glob("*.elf"))
    if not elfs:
        elfs = list(build_dir.rglob("*.elf"))
    if not elfs:
        return ""
    for file_path in elfs:
        if file_path.stem.lower() == project_name.lower():
            return str(file_path.resolve())
    return str(elfs[0].resolve())


def _parse_build_output(output: str) -> dict:
    metrics = {"errors": 0, "warnings": 0, "flash_bytes": 0, "ram_bytes": 0}
    metrics["errors"] = len(re.findall(r":\d+:\d+:\s+error:", output))
    metrics["warnings"] = len(re.findall(r":\d+:\d+:\s+warning:", output))

    for match in re.finditer(r"(FLASH|RAM|CCMRAM)\s*:\s*([\d]+)\s*(B|KB|MB)", output, re.IGNORECASE):
        region = match.group(1).upper()
        value = int(match.group(2))
        unit = match.group(3).upper()
        if unit == "KB":
            value *= 1024
        elif unit == "MB":
            value *= 1024 * 1024
        if region == "FLASH":
            metrics["flash_bytes"] = value
        elif region == "RAM":
            metrics["ram_bytes"] = value
    return metrics


def _extract_first_error(output: str) -> str:
    for line in output.splitlines():
        if re.search(r":\d+:\d+:\s+error:", line):
            return re.sub(r"^\[\d+/\d+\]\s*", "", line).strip()
    return ""


def _error(action: str, code: str, message: str, details: dict | None = None) -> dict:
    return {
        "status": "error",
        "action": action,
        "error": {"code": code, "message": message},
        "details": details or {},
    }


def _build_summary(action: str, status: str, metrics: dict | None = None) -> str:
    metrics = metrics or {}
    if action in ("build", "rebuild"):
        return (
            f"{action} {'成功' if status == 'ok' else '失败'}，"
            f"errors={metrics.get('errors', 0)} warnings={metrics.get('warnings', 0)}"
        )
    if action == "configure":
        return "configure 成功" if status == "ok" else "configure 失败"
    return "clean 成功" if status == "ok" else "clean 失败"


def run_configure(cmake_exe: str, project: str, preset: str, log_dir: str) -> dict:
    project_path = Path(project).resolve()
    if not (project_path / "CMakeLists.txt").exists():
        return _error("configure", "project_not_found", f"CMakeLists.txt 不存在: {project_path}")

    log_path = Path(log_dir).resolve()
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / f"{project_path.name}-{preset}-configure.log"

    try:
        proc = subprocess.run(
            [cmake_exe, "--preset", preset],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(project_path),
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return _error("configure", "timeout", "cmake 配置超时(300s)")
    except FileNotFoundError:
        return _error("configure", "cmake_not_found", f"cmake 不存在: {cmake_exe}")

    output = proc.stdout + "\n" + proc.stderr
    log_file.write_text(output, encoding="utf-8")
    if proc.returncode != 0:
        return _error(
            "configure",
            "configure_failed",
            output.strip()[-500:] or f"cmake configure 返回码: {proc.returncode}",
            {"project": str(project_path), "preset": preset, "log_file": str(log_file.resolve())},
        )
    return {
        "status": "ok",
        "action": "configure",
        "details": {
            "project": str(project_path),
            "preset": preset,
            "log_file": str(log_file.resolve()),
        },
    }


def run_build(cmake_exe: str, project: str, preset: str, log_dir: str) -> dict:
    project_path = Path(project).resolve()
    build_dir = _resolve_build_dir(project_path, preset, project_path / "CMakePresets.json")

    if not (build_dir / "build.ninja").exists() and not (build_dir / "Makefile").exists():
        return _error(
            "build",
            "not_configured",
            f"构建目录不存在或未配置: {build_dir}，请先执行 configure",
            {"project": str(project_path), "preset": preset, "build_dir": str(build_dir.resolve())},
        )

    log_path = Path(log_dir).resolve()
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / f"{project_path.name}-{preset}-build.log"

    try:
        proc = subprocess.run(
            [cmake_exe, "--build", str(build_dir)],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(project_path),
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return _error("build", "timeout", "构建超时(600s)")
    except FileNotFoundError:
        return _error("build", "cmake_not_found", f"cmake 不存在: {cmake_exe}")

    output = proc.stdout + "\n" + proc.stderr
    log_file.write_text(output, encoding="utf-8")
    metrics = _parse_build_output(output)

    details = {
        "project": str(project_path),
        "preset": preset,
        "build_dir": str(build_dir.resolve()),
        "log_file": str(log_file.resolve()),
    }
    if proc.returncode != 0 or metrics["errors"] > 0:
        return {
            "status": "error",
            "action": "build",
            "metrics": metrics,
            "error": {
                "code": "build_failed",
                "message": _extract_first_error(output) or f"构建失败，返回码: {proc.returncode}",
            },
            "details": details,
        }

    elf_file = _find_elf(build_dir, project_path.name)
    if elf_file:
        details["elf_file"] = elf_file
        details["debug_file"] = elf_file
        details["flash_file"] = elf_file

    return {
        "status": "ok",
        "action": "build",
        "metrics": metrics,
        "details": details,
    }


def run_clean(cmake_exe: str, project: str, preset: str, log_dir: str) -> dict:
    project_path = Path(project).resolve()
    build_dir = _resolve_build_dir(project_path, preset, project_path / "CMakePresets.json")

    if not build_dir.exists():
        return {
            "status": "ok",
            "action": "clean",
            "details": {
                "project": str(project_path),
                "preset": preset,
                "build_dir": str(build_dir.resolve()),
                "log_dir": str(Path(log_dir).resolve()),
            },
        }

    if (build_dir / "build.ninja").exists() or (build_dir / "Makefile").exists():
        try:
            proc = subprocess.run(
                [cmake_exe, "--build", str(build_dir), "--target", "clean"],
                capture_output=True,
                text=True,
                timeout=120,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode == 0:
                return {
                    "status": "ok",
                    "action": "clean",
                    "details": {
                        "project": str(project_path),
                        "preset": preset,
                        "build_dir": str(build_dir.resolve()),
                        "mode": "target-clean",
                    },
                }
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    try:
        shutil.rmtree(str(build_dir))
    except OSError as exc:
        return _error("clean", "clean_failed", f"删除构建目录失败: {exc}")

    return {
        "status": "ok",
        "action": "clean",
        "details": {
            "project": str(project_path),
            "preset": preset,
            "build_dir": str(build_dir.resolve()),
            "mode": "remove-tree",
        },
    }


def run_rebuild(cmake_exe: str, project: str, preset: str, log_dir: str) -> dict:
    result = run_clean(cmake_exe, project, preset, log_dir)
    if result["status"] == "error":
        result["action"] = "rebuild"
        return result

    result = run_configure(cmake_exe, project, preset, log_dir)
    if result["status"] == "error":
        result["action"] = "rebuild"
        return result

    result = run_build(cmake_exe, project, preset, log_dir)
    result["action"] = "rebuild"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="GCC 嵌入式工程构建")
    parser.add_argument("action", choices=["configure", "build", "rebuild", "clean"])
    parser.add_argument("--cmake", default=None, help="cmake 可执行文件路径")
    parser.add_argument("--project", default=None, help="工程根目录")
    parser.add_argument("--preset", default=None, help="CMake preset 名称")
    parser.add_argument("--log-dir", default=None, help="日志输出目录")
    parser.add_argument("--config", default=None, help="skill config.json 路径")
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    started_at = now_iso()
    started_ts = time.time()
    workspace = workspace_root(args.workspace)
    
    # 加载三层配置：环境级、工程级、状态
    local_config = load_local_config(__file__)
    project_config = load_project_config(str(workspace))
    state = load_workspace_state(str(workspace))
    last_build = get_state_entry(state, "last_build")

    parameter_sources: dict[str, str] = {}
    try:
        # cmake_exe: CLI > 环境级配置 > 必需
        cmake_exe, parameter_sources["cmake"] = resolve_param(
            "cmake",
            args.cmake,
            config=local_config,
            config_keys=["cmake_exe"],
            required=True,
        )
        
        # project: CLI > 环境级配置 > 工程级配置 > state.json > 必需
        project, parameter_sources["project"] = resolve_param(
            "project",
            args.project,
            config=local_config,
            config_keys=["default_project"],
            normalize_as_path=True,
        )
        # 工程级配置（优先于 state）
        if is_missing(project) and not is_missing(project_config.get("project")):
            project = _resolve_project_path(workspace, project_config.get("project"))
            parameter_sources["project"] = "project_config:project"
        # state.json（最后 fallback）
        if is_missing(project) and not is_missing(last_build.get("project")):
            project = _resolve_project_path(workspace, str(last_build.get("project")))
            parameter_sources["project"] = "state:project"
        if is_missing(project):
            raise ValueError("缺少必要参数: project")
        
        # preset: CLI > 环境级配置 > 工程级配置 > state.json > 必需
        preset, parameter_sources["preset"] = resolve_param(
            "preset",
            args.preset,
            config=local_config,
            config_keys=["default_preset"],
        )
        # 工程级配置（优先于 state）
        if is_missing(preset) and not is_missing(project_config.get("preset")):
            preset = project_config.get("preset")
            parameter_sources["preset"] = "project_config:preset"
        # state.json（最后 fallback）
        if is_missing(preset) and not is_missing(last_build.get("preset")):
            preset = last_build.get("preset")
            parameter_sources["preset"] = "state:preset"
        if is_missing(preset):
            raise ValueError("缺少必要参数: preset")
        
        # log_dir: CLI > 工程级配置 > 环境级配置 > 默认值(.embeddedskills/build)
        log_dir_raw = args.log_dir or project_config.get("log_dir") or local_config.get("log_dir")
        log_dir = _resolve_workspace_path(workspace, log_dir_raw, ".embeddedskills/build")
        if args.log_dir:
            parameter_sources["log_dir"] = "cli"
        elif project_config.get("log_dir"):
            parameter_sources["log_dir"] = "project_config:log_dir"
        elif local_config.get("log_dir"):
            parameter_sources["log_dir"] = "config:log_dir"
        else:
            parameter_sources["log_dir"] = "default"
    except ValueError as exc:
        result = make_result(
            status="error",
            action=args.action,
            summary=str(exc),
            details={},
            context=parameter_context(
                provider="gcc",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
            ),
            error={"code": "missing_param", "message": str(exc)},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    action_map = {
        "configure": run_configure,
        "build": run_build,
        "rebuild": run_rebuild,
        "clean": run_clean,
    }
    raw_result = action_map[args.action](cmake_exe=cmake_exe, project=project, preset=preset, log_dir=log_dir)
    elapsed_ms = (time.time() - started_ts) * 1000

    if raw_result["status"] == "error":
        result = make_result(
            status="error",
            action=raw_result["action"],
            summary=raw_result["error"]["message"],
            details=raw_result.get("details", {}),
            context=parameter_context(
                provider="gcc",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
            ),
            metrics=raw_result.get("metrics", {}),
            error=raw_result["error"],
            timing=make_timing(started_at, elapsed_ms),
        )
    else:
        details = raw_result.get("details", {})
        artifacts = build_artifacts(
            build_dir=details.get("build_dir"),
            elf_file=details.get("elf_file"),
            debug_file=details.get("debug_file"),
            flash_file=details.get("flash_file"),
            log_file=details.get("log_file"),
        )
        metrics = raw_result.get("metrics", {})
        state_info = None
        if args.action in ("build", "rebuild") and raw_result["status"] == "ok":
            state_info = update_state_entry(
                "last_build",
                {
                    "provider": "gcc",
                    "action": args.action,
                    "project": project,
                    "preset": preset,
                    "log_dir": log_dir,
                    "artifacts": artifacts,
                    **artifacts,
                },
                str(workspace),
            )
        summary = _build_summary(args.action, raw_result["status"], metrics)
        next_actions = []
        if artifacts.get("flash_file"):
            next_actions.append("可直接复用 artifacts.flash_file 继续 flash")
        if artifacts.get("debug_file"):
            next_actions.append("可直接复用 artifacts.debug_file 继续 gdb 调试")

        # 构建成功后，将确认过的参数写回工程级配置
        if raw_result["status"] == "ok":
            project_rel = _make_relative_to_workspace(workspace, project)
            save_project_config(
                str(workspace),
                {
                    "project": project_rel,
                    "preset": preset or "",
                    "log_dir": _make_relative_to_workspace(workspace, log_dir),
                },
            )

        result = make_result(
            status=raw_result["status"],
            action=raw_result["action"],
            summary=summary,
            details=details,
            context=parameter_context(
                provider="gcc",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
            ),
            artifacts=artifacts,
            metrics=metrics,
            state=state_info,
            next_actions=next_actions,
            timing=make_timing(started_at, elapsed_ms),
        )

    if args.as_json:
        output_json(result)
        return

    if result["status"] == "ok":
        print(f"[{args.action}] {result['summary']}")
        if result.get("artifacts", {}).get("log_file"):
            print(f"  日志: {result['artifacts']['log_file']}")
        if result.get("artifacts", {}).get("elf_file"):
            print(f"  ELF: {result['artifacts']['elf_file']}")
    else:
        error = result.get("error", {})
        print(f"[{args.action}] 失败 — {error.get('message', result['summary'])}", file=sys.stderr)
        if result.get("details", {}).get("log_file"):
            print(f"  日志: {result['details']['log_file']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
