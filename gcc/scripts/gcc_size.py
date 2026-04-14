"""GCC 嵌入式 ELF 大小分析"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gcc_runtime import hidden_subprocess_kwargs


def _find_size_tool(toolchain_prefix: str, toolchain_path: str) -> str:
    """拼接 size 工具的完整路径"""
    tool_name = f"{toolchain_prefix}size"
    if toolchain_path:
        return str(Path(toolchain_path) / tool_name)
    return tool_name


def _run_size(size_exe: str, elf: str, fmt: str) -> str:
    """调用 arm-none-eabi-size"""
    cmd = [size_exe, f"-{fmt}", elf]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
        **hidden_subprocess_kwargs(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"size 执行失败: {proc.stderr.strip()}")
    return proc.stdout


def _parse_size_berkeley(output: str) -> dict:
    """-B 格式: text data bss dec hex filename"""
    lines = output.strip().splitlines()
    if len(lines) < 2:
        return {}
    parts = lines[1].split()
    if len(parts) < 4:
        return {}
    return {
        "text": int(parts[0]),
        "data": int(parts[1]),
        "bss": int(parts[2]),
        "total": int(parts[3]),
    }


def _parse_size_sysv(output: str) -> list[dict]:
    """-A 格式: section size addr"""
    sections = []
    for line in output.strip().splitlines():
        m = re.match(r"^(\.\S+)\s+(\d+)\s+(0x[0-9a-fA-F]+|\d+)", line)
        if m:
            sections.append({
                "name": m.group(1),
                "size": int(m.group(2)),
                "addr": m.group(3),
            })
    return sections


def _parse_linker_script(ld_path: str) -> dict:
    """从链接脚本解析 MEMORY 区域"""
    content = Path(ld_path).read_text(encoding="utf-8", errors="replace")
    regions = {}
    for m in re.finditer(
        r"(\w+)\s*\([^)]*\)\s*:\s*ORIGIN\s*=\s*(0x[0-9a-fA-F]+)\s*,\s*LENGTH\s*=\s*(\d+)([KMG]?)",
        content, re.IGNORECASE,
    ):
        name = m.group(1).upper()
        origin = int(m.group(2), 16)
        length = int(m.group(3))
        unit = m.group(4).upper()
        if unit == "K":
            length *= 1024
        elif unit == "M":
            length *= 1024 * 1024
        elif unit == "G":
            length *= 1024 * 1024 * 1024
        regions[name] = {"origin": origin, "length": length}
    return regions


def analyze(elf: str, toolchain_prefix: str, toolchain_path: str,
            linker_script: str) -> dict:
    """分析 ELF 文件大小"""
    elf_path = Path(elf).resolve()
    if not elf_path.exists():
        return _error("size", "elf_not_found", f"ELF 文件不存在: {elf_path}")

    size_exe = _find_size_tool(toolchain_prefix, toolchain_path)

    try:
        berkeley_output = _run_size(size_exe, str(elf_path), "B")
        sysv_output = _run_size(size_exe, str(elf_path), "A")
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        return _error("size", "size_failed", str(e))

    berkeley = _parse_size_berkeley(berkeley_output)
    sections = _parse_size_sysv(sysv_output)

    if not berkeley:
        return _error("size", "parse_failed", "无法解析 size 输出")

    summary = dict(berkeley)
    summary["flash_used"] = berkeley["text"] + berkeley["data"]
    summary["ram_used"] = berkeley["data"] + berkeley["bss"]

    details = {
        "elf_file": str(elf_path),
        "sections": sections,
    }

    # 解析链接脚本计算使用率
    if linker_script:
        ld_path = Path(linker_script).resolve()
        if ld_path.exists():
            regions = _parse_linker_script(str(ld_path))
            details["linker_script"] = str(ld_path)
            if "FLASH" in regions:
                flash_total = regions["FLASH"]["length"]
                summary["flash_total"] = flash_total
                summary["flash_percent"] = round(
                    summary["flash_used"] / flash_total * 100, 2
                )
            if "RAM" in regions:
                ram_total = regions["RAM"]["length"]
                summary["ram_total"] = ram_total
                summary["ram_percent"] = round(
                    summary["ram_used"] / ram_total * 100, 2
                )

    return {
        "status": "ok",
        "action": "size",
        "summary": summary,
        "details": details,
    }


def compare(elf1: str, elf2: str, toolchain_prefix: str,
            toolchain_path: str) -> dict:
    """对比两个 ELF 的大小"""
    size_exe = _find_size_tool(toolchain_prefix, toolchain_path)

    results = {}
    for label, path in [("baseline", elf1), ("current", elf2)]:
        elf_path = Path(path).resolve()
        if not elf_path.exists():
            return _error("compare", "elf_not_found", f"ELF 文件不存在: {elf_path}")
        try:
            output = _run_size(size_exe, str(elf_path), "B")
        except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            return _error("compare", "size_failed", str(e))
        parsed = _parse_size_berkeley(output)
        if not parsed:
            return _error("compare", "parse_failed", f"无法解析: {elf_path}")
        parsed["elf"] = str(elf_path)
        results[label] = parsed

    b, c = results["baseline"], results["current"]
    summary = {
        "delta_text": c["text"] - b["text"],
        "delta_data": c["data"] - b["data"],
        "delta_bss": c["bss"] - b["bss"],
        "delta_total": c["total"] - b["total"],
    }

    return {
        "status": "ok",
        "action": "compare",
        "summary": summary,
        "details": results,
    }


def _error(action: str, code: str, message: str) -> dict:
    return {
        "status": "error",
        "action": action,
        "error": {"code": code, "message": message},
    }


def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="GCC 嵌入式 ELF 大小分析")
    sub = parser.add_subparsers(dest="command")

    analyze_p = sub.add_parser("analyze", help="分析 ELF 大小")
    analyze_p.add_argument("--elf", required=True, help="ELF 文件路径")
    analyze_p.add_argument("--toolchain-prefix", default="arm-none-eabi-")
    analyze_p.add_argument("--toolchain-path", default="")
    analyze_p.add_argument("--linker-script", default="", help="链接脚本路径")
    analyze_p.add_argument("--json", action="store_true", dest="as_json")

    compare_p = sub.add_parser("compare", help="对比两个 ELF 大小")
    compare_p.add_argument("--elf", required=True, help="基准 ELF")
    compare_p.add_argument("--compare", required=True, help="对比 ELF")
    compare_p.add_argument("--toolchain-prefix", default="arm-none-eabi-")
    compare_p.add_argument("--toolchain-path", default="")
    compare_p.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    if args.command == "analyze":
        result = analyze(
            elf=args.elf,
            toolchain_prefix=args.toolchain_prefix,
            toolchain_path=args.toolchain_path,
            linker_script=args.linker_script,
        )
        if args.as_json:
            output_json(result)
        else:
            if result["status"] == "ok":
                s = result["summary"]
                print(f"ELF: {result['details']['elf_file']}")
                print(f"  text: {s['text']:>8}  data: {s['data']:>8}  bss: {s['bss']:>8}  total: {s['total']:>8}")
                print(f"  Flash: {s['flash_used']:>8} bytes", end="")
                if "flash_total" in s:
                    print(f" / {s['flash_total']} ({s['flash_percent']}%)", end="")
                print()
                print(f"  RAM:   {s['ram_used']:>8} bytes", end="")
                if "ram_total" in s:
                    print(f" / {s['ram_total']} ({s['ram_percent']}%)", end="")
                print()
            else:
                print(f"错误: {result['error']['message']}", file=sys.stderr)
                sys.exit(1)

    elif args.command == "compare":
        result = compare(
            elf1=args.elf, elf2=args.compare,
            toolchain_prefix=args.toolchain_prefix,
            toolchain_path=args.toolchain_path,
        )
        if args.as_json:
            output_json(result)
        else:
            if result["status"] == "ok":
                s = result["summary"]
                d = result["details"]
                print(f"基准: {d['baseline']['elf']}")
                print(f"对比: {d['current']['elf']}")
                for key in ("delta_text", "delta_data", "delta_bss", "delta_total"):
                    val = s[key]
                    sign = "+" if val > 0 else ""
                    print(f"  {key.replace('delta_', ''):>5}: {sign}{val} bytes")
            else:
                print(f"错误: {result['error']['message']}", file=sys.stderr)
                sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
