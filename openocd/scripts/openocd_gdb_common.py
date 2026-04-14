"""openocd skill 私有 GDB 工具。"""

from __future__ import annotations

import re
import subprocess
from typing import Any


INTROSPECTION_ACTIONS = {
    "backtrace",
    "locals",
    "break",
    "frame",
    "print",
    "watch",
    "threads",
    "disassemble",
    "crash-report",
}


def run_gdb_commands(gdb_exe: str, elf_file: str, target_remote: str, commands: list[str], timeout: int = 30) -> dict:
    gdb_init = ["set pagination off", "set confirm off", "set width 0"]
    if elf_file:
        gdb_init.append(f"file {elf_file}")
    gdb_init.append(f"target remote {target_remote}")

    cmd = [gdb_exe, "--batch", "--nx"]
    for item in gdb_init + commands + ["quit"]:
        cmd.extend(["-ex", item])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"GDB 执行超时({timeout}s)"}
    except Exception as exc:  # pragma: no cover - 兜底异常
        return {"status": "error", "error": str(exc)}


def require_action_expr(action: str, expr: str | None, hint: str) -> str:
    if not expr:
        raise ValueError(f"{action} 必须提供 {hint}")
    return expr


def build_gdb_commands(action: str, expr: str | None = None, *, halt_before: bool = True) -> list[str]:
    commands: list[str] = []
    if halt_before and action in INTROSPECTION_ACTIONS | {"next", "step", "finish", "until"}:
        commands.append("monitor halt")

    if action == "run":
        raise ValueError("run 需要由调用方直接提供 commands")
    if action == "backtrace":
        commands.append("backtrace")
    elif action == "locals":
        commands.append("info locals")
    elif action == "break":
        commands.extend([f"break {require_action_expr(action, expr, '--expr')}", "info breakpoints"])
    elif action == "continue":
        commands.append("monitor resume")
    elif action == "next":
        commands.append("next")
    elif action == "step":
        commands.append("step")
    elif action == "finish":
        commands.append("finish")
    elif action == "until":
        commands.append(f"until {expr}" if expr else "until")
    elif action == "frame":
        commands.append(f"frame {require_action_expr(action, expr, '--expr <帧号>')}")
    elif action == "print":
        commands.append(f"print {require_action_expr(action, expr, '--expr')}")
    elif action == "watch":
        commands.extend([f"watch {require_action_expr(action, expr, '--expr')}", "info breakpoints"])
    elif action == "disassemble":
        commands.append(f"disassemble {expr}" if expr else "disassemble")
    elif action == "threads":
        commands.extend(["info threads", "thread apply all backtrace 1"])
    elif action == "crash-report":
        commands.extend(
            [
                "backtrace full",
                "info registers",
                "frame 0",
                "info locals",
                "info threads",
                "disassemble /m $pc,$pc+32",
            ]
        )
    else:
        raise ValueError(f"未知 GDB 子命令: {action}")

    return commands


def _parse_frames(stdout: str) -> list[dict[str, Any]]:
    frames = []
    for line in stdout.splitlines():
        match = re.match(
            r"#(?P<index>\d+)\s+(?:(?P<address>0x[0-9a-fA-F]+)\s+in\s+)?(?P<function>[^\s(]+)?\s*\((?P<args>[^)]*)\)(?:\s+at\s+(?P<location>.+))?",
            line.strip(),
        )
        if not match:
            continue
        frame = {
            "frame": int(match.group("index")),
            "function": match.group("function") or "??",
        }
        if match.group("address"):
            frame["address"] = match.group("address")
        if match.group("args"):
            frame["args"] = match.group("args").strip()
        if match.group("location"):
            frame["location"] = match.group("location").strip()
        frames.append(frame)
    return frames


def _parse_variables(stdout: str) -> dict[str, str]:
    variables: dict[str, str] = {}
    for line in stdout.splitlines():
        match = re.match(r"^([A-Za-z_][\w.\->\[\]]*)\s*=\s*(.+)$", line.strip())
        if match:
            variables[match.group(1)] = match.group(2).strip()
    return variables


def _parse_registers(stdout: str) -> dict[str, str]:
    registers: dict[str, str] = {}
    for line in stdout.splitlines():
        match = re.match(r"^([A-Za-z_][\w]*)\s+(0x[0-9a-fA-F]+)\b(.*)$", line.strip())
        if match:
            registers[match.group(1)] = match.group(2)
    return registers


def _parse_threads(stdout: str) -> list[dict[str, Any]]:
    threads: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        match = re.match(r"^([* ])\s*(\d+)\s+Thread\s+(.+)$", line.strip())
        if match:
            threads.append(
                {
                    "selected": match.group(1) == "*",
                    "id": int(match.group(2)),
                    "description": match.group(3).strip(),
                }
            )
    return threads


def _parse_disassembly(stdout: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for line in stdout.splitlines():
        match = re.match(r"^(=>)?\s*(0x[0-9a-fA-F]+)(?:\s+<([^>]+)>)?:\s+(.+)$", line.strip())
        if match:
            item = {
                "address": match.group(2),
                "instruction": match.group(4).strip(),
            }
            if match.group(1):
                item["selected"] = "true"
            if match.group(3):
                item["symbol"] = match.group(3).strip()
            items.append(item)
    return items


def _extract_source_location(stdout: str, frames: list[dict[str, Any]]) -> str:
    for frame in frames:
        location = frame.get("location", "")
        if location:
            return location
    match = re.search(r'at\s+([A-Za-z]:)?[^:\n]+\:\d+', stdout)
    return match.group(0).replace("at ", "").strip() if match else ""


def _parse_selected_frame(stdout: str) -> dict[str, Any]:
    match = re.search(r"#(?P<index>\d+)\s+.+?(?:at\s+(?P<location>.+))?$", stdout, re.MULTILINE)
    if not match:
        return {}
    selected = {"frame": int(match.group("index"))}
    if match.group("location"):
        selected["location"] = match.group("location").strip()
    return selected


def parse_gdb_output(stdout: str, action: str) -> dict:
    frames = _parse_frames(stdout)
    variables = _parse_variables(stdout)
    registers = _parse_registers(stdout)
    threads = _parse_threads(stdout)
    disassembly = _parse_disassembly(stdout)

    parsed: dict[str, Any] = {"output": stdout}
    if frames:
        parsed["frames"] = frames
    if variables:
        parsed["variables"] = variables
    if registers:
        parsed["registers"] = registers
    if threads:
        parsed["threads"] = threads
    if disassembly:
        parsed["disassembly"] = disassembly

    selected_frame = _parse_selected_frame(stdout)
    if not selected_frame and frames:
        selected_frame = frames[0]
    if selected_frame:
        parsed["selected_frame"] = selected_frame

    source_location = _extract_source_location(stdout, frames)
    if source_location:
        parsed["source_location"] = source_location

    if action == "print":
        match = re.search(r"\$\d+\s*=\s*(.+)", stdout)
        if match:
            parsed["value"] = match.group(1).strip()

    return parsed
