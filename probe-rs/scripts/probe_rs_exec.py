"""probe-rs 基础操作与包装命令。"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from probe_rs_runtime import (
    build_artifacts,
    default_config_path,
    get_state_entry,
    hidden_subprocess_kwargs,
    is_missing,
    load_json_file,
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
    resolve_tool_param,
    save_project_config,
    update_state_entry,
    workspace_root,
)


ALL_ACTIONS = ["list", "info", "flash", "erase", "reset", "read-mem", "write-mem", "attach", "run"]

ERROR_PATTERNS = [
    (r"no probes were found", "no_probe_found", "未检测到调试探针，请检查 USB 连接和驱动"),
    (r"multiple probes were found", "multiple_probes", "检测到多个探针，请通过 --probe 显式指定"),
    (r"chip.*not found", "chip_not_found", "未找到目标芯片描述，请确认 --chip 配置"),
    (r"failed to attach", "attach_failed", "连接目标失败，请检查供电、连线和芯片型号"),
    (r"permission denied", "permission_denied", "访问调试探针被拒绝，请检查驱动和权限"),
    (r"address.*out of bounds", "address_out_of_range", "访问地址超出范围，请确认地址和数据宽度"),
    (r"timed out", "timeout", "操作超时，请检查连接和速度配置"),
]


def infer_binary_format(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    if suffix == ".bin":
        return "bin"
    if suffix in {".hex", ".ihex"}:
        return "hex"
    if suffix == ".uf2":
        return "uf2"
    return "elf"


def parse_output(text: str, action: str) -> dict:
    parsed = {"raw": text}
    for pattern, code, message in ERROR_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return {"error_code": code, "error_message": message, "raw": text}

    if action == "list":
        probes = []
        for line in text.splitlines():
            item = line.strip()
            if not item or item.lower().startswith("the following debug probes were found"):
                continue
            probes.append(item)
        if probes:
            parsed["probes"] = probes
    elif action == "read-mem":
        words = re.findall(r"\b[0-9a-fA-F]{2,16}\b", text)
        if words:
            parsed["words"] = words
    elif action == "info":
        chip_match = re.search(r"chip[:=]\s*([^\r\n]+)", text, re.IGNORECASE)
        probe_match = re.search(r"probe[:=]\s*([^\r\n]+)", text, re.IGNORECASE)
        if chip_match:
            parsed["chip"] = chip_match.group(1).strip()
        if probe_match:
            parsed["probe"] = probe_match.group(1).strip()
    return parsed


def _summary(action: str, parsed: dict, fallback: str) -> str:
    if action == "list" and parsed.get("probes"):
        return f"已发现 {len(parsed['probes'])} 个调试探针"
    if action == "flash":
        return "烧录成功"
    if action == "erase":
        return "擦除成功"
    if action == "reset":
        return "目标已复位"
    if action == "read-mem" and parsed.get("words"):
        return f"已读取 {len(parsed['words'])} 个内存字"
    if action == "write-mem":
        return "内存写入成功"
    return fallback


def normalize_write_values(value_text: str) -> list[str]:
    values = [item.strip() for item in re.split(r"[\s,]+", value_text) if item.strip()]
    if not values:
        raise ValueError("write-mem 必须提供 --value")

    normalized: list[str] = []
    for value in values:
        lowered = value.lower()
        if lowered.startswith(("0x", "0o", "0b")):
            normalized.append(value)
            continue
        if re.fullmatch(r"[0-9a-fA-F]+", value) and (value.startswith("0") or re.search(r"[a-fA-F]", value)):
            normalized.append(f"0x{value}")
            continue
        normalized.append(value)
    return normalized


def _state_lookup(state: dict) -> dict:
    last_build = get_state_entry(state, "last_build")
    last_flash = get_state_entry(state, "last_flash")
    last_debug = get_state_entry(state, "last_debug")
    artifacts = last_build.get("artifacts", {})
    return {
        "chip": last_debug.get("chip") or last_flash.get("chip"),
        "probe": last_debug.get("probe") or last_flash.get("probe"),
        "protocol": last_debug.get("protocol") or last_flash.get("protocol"),
        "speed": last_debug.get("speed") or last_flash.get("speed"),
        "connect_under_reset": last_debug.get("connect_under_reset") or last_flash.get("connect_under_reset"),
        "elf_file": last_build.get("debug_file") or artifacts.get("debug_file"),
        "flash_file": last_build.get("flash_file") or artifacts.get("flash_file"),
    }


def resolve_probe_params(args, config: dict, project_config: dict, state_lookup: dict, workspace: str) -> tuple[dict, dict]:
    parameter_sources: dict[str, str] = {}

    exe, parameter_sources["exe"] = resolve_tool_param(
        "exe",
        args.exe,
        local_config=config,
        local_keys=["exe"],
        path_candidates=["probe-rs.exe", "probe-rs"],
        default="probe-rs",
    )
    chip, parameter_sources["chip"] = resolve_project_param(
        "chip",
        args.chip,
        project_config=project_config,
        project_keys=["chip"],
        state_record=state_lookup,
        state_keys=["chip"],
    )
    protocol, parameter_sources["protocol"] = resolve_project_param(
        "protocol",
        args.protocol,
        project_config=project_config,
        project_keys=["protocol"],
        state_record=state_lookup,
        state_keys=["protocol"],
        default="swd",
    )
    probe, parameter_sources["probe"] = resolve_project_param(
        "probe",
        args.probe,
        project_config=project_config,
        project_keys=["probe"],
        state_record=state_lookup,
        state_keys=["probe"],
    )
    speed, parameter_sources["speed"] = resolve_project_param(
        "speed",
        args.speed,
        project_config=project_config,
        project_keys=["speed"],
        state_record=state_lookup,
        state_keys=["speed"],
        default="4000",
    )

    connect_under_reset = args.connect_under_reset
    connect_source = "cli" if args.connect_under_reset else ""
    if not connect_under_reset:
        value = project_config.get("connect_under_reset")
        if value is not None:
            connect_under_reset = bool(value)
            connect_source = "project_config"
    if not connect_under_reset:
        value = state_lookup.get("connect_under_reset")
        if value is not None:
            connect_under_reset = bool(value)
            connect_source = "state"
    if not connect_source:
        connect_source = "default"
    parameter_sources["connect_under_reset"] = connect_source

    file_keys = ["file", "flash_file"] if args.action == "flash" else ["file", "elf_file"]
    file_path, parameter_sources["file"] = resolve_artifact_param(
        "file",
        args.file,
        project_config=project_config,
        project_keys=["file", "default_file"],
        state_record=state_lookup,
        state_keys=file_keys,
        normalize_as_path=True,
        workspace=workspace,
    )

    return (
        {
            "exe": exe,
            "chip": chip,
            "protocol": protocol,
            "probe": probe,
            "speed": str(speed),
            "connect_under_reset": bool(connect_under_reset),
            "file": file_path,
            "workspace": workspace,
        },
        parameter_sources,
    )


def build_probe_args(params: dict, *, require_chip: bool = True) -> list[str]:
    args = ["--non-interactive"]
    if require_chip:
        if is_missing(params["chip"]):
            raise ValueError("缺少必要参数: chip")
        args.extend(["--chip", params["chip"]])
    if params.get("protocol"):
        args.extend(["--protocol", str(params["protocol"]).lower()])
    if params.get("probe"):
        args.extend(["--probe", params["probe"]])
    if params.get("speed"):
        args.extend(["--speed", str(params["speed"])])
    if params.get("connect_under_reset"):
        args.append("--connect-under-reset")
    return args


def build_command(action: str, params: dict, args) -> list[str]:
    exe = params["exe"]
    if action == "list":
        return [exe, "list"]
    if action == "info":
        return [exe, "info", *build_probe_args(params)]
    if action == "reset":
        return [exe, "reset", *build_probe_args(params)]
    if action == "erase":
        return [exe, "erase", *build_probe_args(params)]
    if action == "read-mem":
        return [exe, "read", *build_probe_args(params), args.width, args.address, args.length]
    if action == "write-mem":
        return [exe, "write", *build_probe_args(params), args.width, args.address, *normalize_write_values(args.value)]
    if action == "flash":
        if is_missing(params["file"]):
            raise ValueError("flash 必须提供 --file 固件文件路径")
        if not os.path.isfile(params["file"]):
            raise ValueError(f"固件文件不存在: {params['file']}")
        fmt = infer_binary_format(params["file"])
        cmd = [exe, "download", *build_probe_args(params), "--binary-format", fmt]
        if args.chip_erase:
            cmd.append("--chip-erase")
        if args.verify:
            cmd.append("--verify")
        if fmt == "bin":
            if not args.address:
                raise ValueError(".bin 文件必须提供 --address 烧录地址")
            cmd.extend(["--base-address", args.address])
        cmd.append(params["file"])
        return cmd
    if action in {"attach", "run"}:
        cmd = [exe, action, *build_probe_args(params)]
        if params.get("file"):
            cmd.append(params["file"])
        return cmd
    raise ValueError(f"未知动作: {action}")


def run_command(action: str, cmd: list[str], duration: float = 0) -> dict:
    started = time.time()
    try:
        if action in {"attach", "run"} and duration > 0:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                **hidden_subprocess_kwargs(new_process_group=True),
            )
            time.sleep(duration)
            if proc.poll() is None:
                proc.terminate()
            stdout, stderr = proc.communicate(timeout=5)
        else:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120 if action not in {"attach", "run"} else None,
                **hidden_subprocess_kwargs(),
            )
            stdout, stderr = proc.stdout, proc.stderr
        returncode = proc.returncode
    except FileNotFoundError:
        return {"status": "error", "action": action, "error": {"code": "exe_not_found", "message": f"probe-rs 不存在或不在 PATH 中: {cmd[0]}"}}
    except subprocess.TimeoutExpired:
        return {"status": "error", "action": action, "error": {"code": "timeout", "message": "probe-rs 执行超时(120s)"}}
    except Exception as exc:
        return {"status": "error", "action": action, "error": {"code": "exec_error", "message": str(exc)}}

    elapsed_ms = int((time.time() - started) * 1000)
    combined = "\n".join(part for part in (stdout, stderr) if part)
    parsed = parse_output(combined, action)
    if "error_code" in parsed:
        return {
            "status": "error",
            "action": action,
            "error": {"code": parsed["error_code"], "message": parsed["error_message"]},
            "details": {"elapsed_ms": elapsed_ms, "returncode": returncode},
        }

    status = "ok"
    if returncode != 0 and action not in {"attach", "run"}:
        status = "error"
    return {
        "status": status,
        "action": action,
        "summary": _summary(action, parsed, f"{action} 完成"),
        "details": {"elapsed_ms": elapsed_ms, "returncode": returncode, **{k: v for k, v in parsed.items() if k != "raw"}, "output": combined},
        "error": None if status == "ok" else {"code": "nonzero_exit", "message": combined or f"{action} 失败"},
    }


def state_payload(action: str, params: dict) -> tuple[str, dict] | None:
    payload = {
        "provider": "probe-rs",
        "action": action,
        "chip": params["chip"] or "",
        "probe": params["probe"] or "",
        "protocol": params["protocol"],
        "speed": params["speed"],
        "connect_under_reset": params["connect_under_reset"],
    }
    if action == "flash":
        payload["flash_file"] = params["file"] or ""
        payload["artifacts"] = build_artifacts(flash_file=params["file"])
        return "last_flash", payload
    if action in {"reset", "read-mem", "write-mem", "info"}:
        return "last_debug", payload
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="probe-rs 基础操作包装")
    parser.add_argument("action", choices=ALL_ACTIONS)
    parser.add_argument("--exe", default=None, help="probe-rs 可执行文件路径或命令名")
    parser.add_argument("--chip", default=None, help="芯片型号")
    parser.add_argument("--protocol", default=None, choices=["swd", "jtag"], help="调试协议")
    parser.add_argument("--probe", default=None, help="探针选择器，格式 VID:PID[:Serial]")
    parser.add_argument("--speed", default=None, help="调试速率 kHz")
    parser.add_argument("--connect-under-reset", action="store_true", help="连接时保持 reset")
    parser.add_argument("--file", default=None, help="固件或 ELF 文件路径")
    parser.add_argument("--address", default="", help="地址（flash .bin / read-mem / write-mem 用）")
    parser.add_argument("--length", default="64", help="读取长度")
    parser.add_argument("--value", default="", help="写入值")
    parser.add_argument("--width", default="b32", choices=["b8", "b16", "b32", "b64"], help="读写位宽")
    parser.add_argument("--duration", type=float, default=0, help="attach/run 时运行秒数，0 表示等待命令自然退出")
    parser.add_argument("--verify", action="store_true", help="烧录后校验")
    parser.add_argument("--chip-erase", action="store_true", help="烧录前整片擦除")
    parser.add_argument("--config", default=None, help="skill config.json 路径")
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
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

    params, parameter_sources = resolve_probe_params(args, config, project_config, state_lookup, str(workspace))

    if args.action != "list" and is_missing(params["chip"]):
        result = make_result(
            status="error",
            action=args.action,
            summary="缺少必要参数: chip",
            context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "missing_chip", "message": "必须提供 --chip，或通过 .embeddedskills/config.json 的 probe-rs 段配置"},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    try:
        cmd = build_command(args.action, params, args)
    except ValueError as exc:
        result = make_result(
            status="error",
            action=args.action,
            summary=str(exc),
            context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "invalid_args", "message": str(exc)},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    raw_result = run_command(args.action, cmd, args.duration)
    elapsed_ms = (time.time() - started_ts) * 1000
    if raw_result.get("status") == "ok":
        save_project_config(str(workspace), {
            "chip": params["chip"] or "",
            "protocol": params["protocol"],
            "probe": params["probe"] or "",
            "speed": params["speed"],
            "connect_under_reset": params["connect_under_reset"],
        })
        state_info = {}
        state_entry = state_payload(args.action, params)
        if state_entry:
            state_key, payload = state_entry
            state_info = update_state_entry(state_key, payload, str(workspace))
        result = make_result(
            status="ok",
            action=args.action,
            summary=raw_result.get("summary", f"{args.action} 完成"),
            details={
                "chip": params["chip"] or "",
                "probe": params["probe"] or "",
                "protocol": params["protocol"],
                "speed": params["speed"],
                "command": cmd,
                **(raw_result.get("details") or {}),
            },
            context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            artifacts=build_artifacts(flash_file=params["file"] if args.action == "flash" else "", debug_file=params["file"] if args.action in {"attach", "run"} else ""),
            state=state_info,
            next_actions=["可继续基于 probe-rs 执行 gdb 或 rtt 观测"] if args.action in {"flash", "info"} else None,
            timing=make_timing(started_at, elapsed_ms),
        )
    else:
        result = make_result(
            status="error",
            action=args.action,
            summary=(raw_result.get("error") or {}).get("message", f"{args.action} 失败"),
            details={
                "chip": params["chip"] or "",
                "probe": params["probe"] or "",
                "protocol": params["protocol"],
                "speed": params["speed"],
                "command": cmd,
                **(raw_result.get("details") or {}),
            },
            context=parameter_context(provider="probe-rs", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error=raw_result.get("error"),
            timing=make_timing(started_at, elapsed_ms),
        )

    if args.as_json:
        output_json(result)
    elif result["status"] == "ok":
        print(f"[probe-rs {args.action}] {result['summary']}")
        output = result.get("details", {}).get("output", "")
        if output:
            print(output)
    else:
        print(f"[probe-rs {args.action}] 失败 — {result['error']['message']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
