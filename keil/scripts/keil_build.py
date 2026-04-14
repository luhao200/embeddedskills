"""Keil MDK 构建 / 重建 / 清理 / 烧录。"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from keil_runtime import (  # noqa: E402
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


ERRORLEVEL_MAP = {
    0: ("ok", "无错误或警告"),
    1: ("ok", "有警告"),
    2: ("error", "有错误"),
    3: ("error", "致命错误"),
    11: ("error", "无法打开工程文件"),
    12: ("error", "设备数据库缺失"),
    13: ("error", "写入错误"),
    15: ("error", "UV4 访问错误"),
    20: ("error", "未知错误"),
}

ACTION_FLAG = {
    "build": "-b",
    "rebuild": "-r",
    "clean": "-c",
    "flash": "-f",
}

UV4_TIMEOUT_SEC = 1800

GENERATED_OUTPUT_SUFFIXES = {
    ".axf",
    ".elf",
    ".hex",
    ".bin",
    ".o",
    ".d",
    ".crf",
    ".dep",
    ".htm",
    ".lnp",
    ".iex",
}


def parse_log(log_path: str) -> dict:
    metrics = {"errors": 0, "warnings": 0, "flash_bytes": 0, "ram_bytes": 0}
    if not os.path.isfile(log_path):
        return metrics

    with open(log_path, "r", encoding="utf-8", errors="replace") as file_obj:
        content = file_obj.read()

    error_match = re.search(r"(\d+)\s+Error\(s\)\s*,\s*(\d+)\s+Warning\(s\)", content)
    if error_match:
        metrics["errors"] = int(error_match.group(1))
        metrics["warnings"] = int(error_match.group(2))

    size_match = re.search(
        r"Program Size:\s+Code=(\d+)\s+RO-data=(\d+)\s+RW-data=(\d+)\s+ZI-data=(\d+)",
        content,
    )
    if size_match:
        code_size = int(size_match.group(1))
        ro_data = int(size_match.group(2))
        rw_data = int(size_match.group(3))
        zi_data = int(size_match.group(4))
        metrics["flash_bytes"] = code_size + ro_data + rw_data
        metrics["ram_bytes"] = rw_data + zi_data

    return metrics


def _resolve_path(base_dir: Path, raw_path: str) -> Path:
    raw_path = (raw_path or "").strip()
    if not raw_path:
        return base_dir
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


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


def _collect_target_artifacts(project_path: Path, target: str) -> dict:
    if project_path.suffix.lower() != ".uvprojx":
        return {}

    try:
        root = ET.parse(str(project_path)).getroot()
    except (ET.ParseError, OSError):
        return {}

    target_el = None
    fallback_target = None
    for item in root.iter("Target"):
        name_el = item.find("TargetName")
        if name_el is None or not name_el.text:
            continue
        if fallback_target is None:
            fallback_target = item
        if target and name_el.text.strip() == target:
            target_el = item
            break

    if target_el is None:
        target_el = fallback_target
    if target_el is None:
        return {}

    common = target_el.find("TargetOption/TargetCommonOption")
    if common is None:
        return {}

    output_dir = _resolve_path(project_path.parent, common.findtext("OutputDirectory", default=""))
    output_name = common.findtext("OutputName", default="").strip() or project_path.stem

    candidates = {
        "axf_file": output_dir / f"{output_name}.axf",
        "elf_file": output_dir / f"{output_name}.elf",
        "hex_file": output_dir / f"{output_name}.hex",
        "bin_file": output_dir / f"{output_name}.bin",
    }

    details: dict[str, str] = {}
    for key, file_path in candidates.items():
        if file_path.exists():
            details[key] = str(file_path.resolve())

    if not details and output_dir.exists():
        for suffix, key in (
            (".axf", "axf_file"),
            (".elf", "elf_file"),
            (".hex", "hex_file"),
            (".bin", "bin_file"),
        ):
            matches = sorted(output_dir.rglob(f"{output_name}*{suffix}"), key=lambda path: path.stat().st_mtime, reverse=True)
            if matches:
                details[key] = str(matches[0].resolve())

    debug_file = details.get("elf_file") or details.get("axf_file")
    flash_file = details.get("hex_file") or details.get("bin_file") or debug_file
    if debug_file:
        details["debug_file"] = debug_file
    if flash_file:
        details["flash_file"] = flash_file
    if output_dir.exists():
        details["output_dir"] = str(output_dir.resolve())
    return details


def _target_common_option(project_path: Path, target: str) -> tuple[Path | None, str]:
    if project_path.suffix.lower() != ".uvprojx":
        return None, ""

    try:
        root = ET.parse(str(project_path)).getroot()
    except (ET.ParseError, OSError):
        return None, ""

    target_el = None
    fallback_target = None
    for item in root.iter("Target"):
        name_el = item.find("TargetName")
        if name_el is None or not name_el.text:
            continue
        if fallback_target is None:
            fallback_target = item
        if target and name_el.text.strip() == target:
            target_el = item
            break

    if target_el is None:
        target_el = fallback_target
    if target_el is None:
        return None, ""

    common = target_el.find("TargetOption/TargetCommonOption")
    if common is None:
        return None, ""

    output_dir = _resolve_path(project_path.parent, common.findtext("OutputDirectory", default=""))
    output_name = common.findtext("OutputName", default="").strip() or project_path.stem
    return output_dir if output_dir.exists() else None, output_name


def _cleanup_generated_outputs(project_path: Path, target: str) -> dict:
    output_dir, output_name = _target_common_option(project_path, target)
    if output_dir is None:
        return {}

    removed: list[str] = []
    prefix = output_name.lower()
    for item in output_dir.iterdir():
        if not item.is_file():
            continue
        suffix = item.suffix.lower()
        name = item.name.lower()
        should_remove = suffix in GENERATED_OUTPUT_SUFFIXES or name.startswith(prefix)
        if not should_remove:
            continue
        try:
            item.unlink()
            removed.append(str(item.resolve()))
        except OSError:
            continue

    details: dict[str, object] = {
        "output_dir": str(output_dir.resolve()),
        "removed_files": removed,
        "removed_count": len(removed),
    }
    return details


def _build_summary(action: str, status: str, metrics: dict) -> str:
    errors = metrics.get("errors", 0)
    warnings = metrics.get("warnings", 0)
    if status == "error":
        return f"{action} 失败，errors={errors} warnings={warnings}"
    if action in ("build", "rebuild"):
        return f"{action} 成功，errors={errors} warnings={warnings}"
    return f"{action} 成功"


def _next_actions(action: str, artifacts: dict) -> list[str]:
    actions: list[str] = []
    if action in ("build", "rebuild") and artifacts.get("flash_file"):
        actions.append("可直接复用 artifacts.flash_file 继续 flash")
    if action in ("build", "rebuild") and artifacts.get("debug_file"):
        actions.append("可直接复用 artifacts.debug_file 继续 gdb 调试")
    return actions


def _extract_uv4_error(action: str, proc: subprocess.CompletedProcess[str], log_file: Path, errorlevel_desc: str) -> dict:
    log_tail = ""
    if log_file.exists():
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail = "\n".join(lines[-40:])
        except OSError:
            log_tail = ""

    combined = "\n".join(part for part in (proc.stdout, proc.stderr, log_tail) if part).strip()
    if action == "flash":
        flash_failed = re.search(r"(Flash Download failed\s+-\s+.+)", combined, re.IGNORECASE)
        if flash_failed:
            return {"code": "flash_failed", "message": flash_failed.group(1).strip()}
        if re.search(r"Programming Failed!", combined, re.IGNORECASE):
            return {"code": "flash_failed", "message": "Programming Failed"}

    patterns = [
        (r"\*\*\*\s*error\s+\d+:\s+(.+)", "uv4_error"),
        (r"Error:\s+(.+)", "uv4_error"),
    ]
    for pattern, code in patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            return {"code": code, "message": match.group(1).strip()}

    return {
        "code": f"{action}_failed",
        "message": errorlevel_desc or "UV4 执行失败",
    }


def run_uv4(uv4_exe: str, action: str, project: str, target: str, log_dir: str, clean_first: bool = False) -> dict:
    project_path = Path(project).resolve()
    if not project_path.exists():
        return {
            "status": "error",
            "action": action,
            "error": {"code": "project_not_found", "message": f"工程文件不存在: {project_path}"},
        }

    if not os.path.isfile(uv4_exe):
        return {
            "status": "error",
            "action": action,
            "error": {"code": "uv4_not_found", "message": f"UV4.exe 不存在: {uv4_exe}"},
        }

    log_path = Path(log_dir).resolve()
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / f"{project_path.stem}-{target or 'default'}-{action}.log"

    flag = ACTION_FLAG[action]
    if action == "rebuild" and clean_first:
        flag = "-cr"

    cmd = [uv4_exe, flag, str(project_path), "-j0", "-o", str(log_file)]
    if target:
        cmd.extend(["-t", target])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=UV4_TIMEOUT_SEC,
            cwd=str(project_path.parent),
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "action": action,
            "error": {"code": "timeout", "message": f"UV4.exe 执行超时({UV4_TIMEOUT_SEC}s)"},
        }
    except Exception as exc:  # pragma: no cover - 兜底异常
        return {
            "status": "error",
            "action": action,
            "error": {"code": "exec_error", "message": str(exc)},
        }

    if action == "clean":
        cleanup_details = _cleanup_generated_outputs(project_path, target)
    else:
        cleanup_details = {}

    metrics = parse_log(str(log_file))
    _, errorlevel_desc = ERRORLEVEL_MAP.get(proc.returncode, ("error", f"未知返回码: {proc.returncode}"))
    status = "error" if proc.returncode >= 2 or metrics["errors"] > 0 else "ok"

    details = {
        "project": str(project_path),
        "target": target,
        "log_file": str(log_file.resolve()),
        "errorlevel": proc.returncode,
        "errorlevel_desc": errorlevel_desc,
        **_collect_target_artifacts(project_path, target),
        **cleanup_details,
    }

    result = {
        "status": status,
        "action": action,
        "metrics": metrics,
        "details": details,
    }
    if status == "error":
        result["error"] = _extract_uv4_error(action, proc, log_file, errorlevel_desc)

    return result


def check_last_build_ok(log_dir: str, project: str, target: str) -> bool:
    log_path = Path(log_dir).resolve()
    stem = Path(project).stem
    for action in ("build", "rebuild"):
        log_file = log_path / f"{stem}-{target or 'default'}-{action}.log"
        if log_file.exists():
            metrics = parse_log(str(log_file))
            if metrics["errors"] == 0:
                return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Keil MDK 构建/重建/清理/烧录")
    parser.add_argument("action", choices=["build", "rebuild", "clean", "flash"])
    parser.add_argument("--uv4", default=None, help="UV4.exe 路径")
    parser.add_argument("--project", default=None, help="工程文件路径")
    parser.add_argument("--target", default=None, help="Target 名称")
    parser.add_argument("--log-dir", default=None, help="日志输出目录")
    parser.add_argument("--clean-first", action="store_true", help="rebuild 时先 clean")
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
        # uv4_exe: CLI > 环境级配置 > 必需
        uv4_exe, parameter_sources["uv4"] = resolve_param(
            "uv4",
            args.uv4,
            config=local_config,
            config_keys=["uv4_exe"],
            required=True,
            normalize_as_path=True,
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
        
        # target: CLI > 环境级配置 > 工程级配置 > state.json
        target, parameter_sources["target"] = resolve_param(
            "target",
            args.target,
            config=local_config,
            config_keys=["default_target"],
        )
        # 工程级配置（优先于 state）
        if is_missing(target) and not is_missing(project_config.get("target")):
            target = project_config.get("target")
            parameter_sources["target"] = "project_config:target"
        # state.json（最后 fallback）
        if is_missing(target) and not is_missing(last_build.get("target")):
            target = last_build.get("target")
            parameter_sources["target"] = "state:target"
        
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
                provider="keil",
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

    if args.action == "flash" and not check_last_build_ok(log_dir, project, target or ""):
        result = make_result(
            status="error",
            action="flash",
            summary="最近构建不可用于 flash",
            details={"project": project, "target": target, "log_dir": log_dir},
            context=parameter_context(
                provider="keil",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
            ),
            error={
                "code": "build_not_clean",
                "message": "最近一次构建存在错误或无构建记录，禁止继续烧录。请先执行 build 并确认无错误。",
            },
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    raw_result = run_uv4(
        uv4_exe=uv4_exe,
        action=args.action,
        project=project,
        target=target or "",
        log_dir=log_dir,
        clean_first=args.clean_first,
    )
    elapsed_ms = (time.time() - started_ts) * 1000

    if raw_result["status"] == "error":
        result = make_result(
            status="error",
            action=args.action,
            summary=raw_result["error"]["message"],
            details=raw_result.get("details", {}),
            context=parameter_context(
                provider="keil",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
            ),
            error=raw_result["error"],
            timing=make_timing(started_at, elapsed_ms),
        )
    else:
        details = raw_result["details"]
        artifacts = build_artifacts(
            axf_file=details.get("axf_file"),
            elf_file=details.get("elf_file"),
            hex_file=details.get("hex_file"),
            bin_file=details.get("bin_file"),
            flash_file=details.get("flash_file"),
            debug_file=details.get("debug_file"),
            output_dir=details.get("output_dir"),
            log_file=details.get("log_file"),
        )
        summary = _build_summary(args.action, raw_result["status"], raw_result["metrics"])
        state_info = None
        if args.action in ("build", "rebuild") and raw_result["status"] == "ok":
            state_info = update_state_entry(
                "last_build",
                {
                    "provider": "keil",
                    "action": args.action,
                    "project": project,
                    "target": target,
                    "log_dir": log_dir,
                    "artifacts": artifacts,
                    **artifacts,
                },
                str(workspace),
            )
        elif args.action == "flash" and raw_result["status"] == "ok":
            state_info = update_state_entry(
                "last_flash",
                {
                    "provider": "keil",
                    "action": args.action,
                    "project": project,
                    "target": target,
                    "artifacts": artifacts,
                    **artifacts,
                },
                str(workspace),
            )

        # 构建成功后，将确认过的参数写回工程级配置
        if raw_result["status"] == "ok":
            project_rel = _make_relative_to_workspace(workspace, project)
            save_project_config(
                str(workspace),
                {
                    "project": project_rel,
                    "target": target or "",
                    "log_dir": _make_relative_to_workspace(workspace, log_dir),
                },
            )

        result = make_result(
            status=raw_result["status"],
            action=args.action,
            summary=summary,
            details=details,
            context=parameter_context(
                provider="keil",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
            ),
            artifacts=artifacts,
            metrics=raw_result["metrics"],
            state=state_info,
            next_actions=_next_actions(args.action, artifacts),
            timing=make_timing(started_at, elapsed_ms),
        )

    if args.as_json:
        output_json(result)
        return

    if result["status"] == "ok":
        print(f"[{args.action}] {result['summary']}")
        if result.get("artifacts", {}).get("log_file"):
            print(f"  日志: {result['artifacts']['log_file']}")
        if result.get("artifacts", {}).get("flash_file"):
            print(f"  Flash: {result['artifacts']['flash_file']}")
        if result.get("artifacts", {}).get("debug_file"):
            print(f"  Debug: {result['artifacts']['debug_file']}")
    else:
        error = result.get("error", {})
        print(f"[{args.action}] 失败 — {error.get('message', result['summary'])}", file=sys.stderr)
        if result.get("details", {}).get("log_file"):
            print(f"  日志: {result['details']['log_file']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
