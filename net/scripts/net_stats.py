#!/usr/bin/env python3
"""基于 tshark 的流量统计工具，按协议、端点或端口输出。"""

import argparse
import io
import json
import os
import re
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def check_tshark(exe):
    try:
        result = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def run_tshark_stats(exe, iface, duration, mode, interval, display_filter=""):
    """运行 tshark 统计并解析结果。"""
    cmd = [exe, "-i", str(iface), "-a", f"duration:{duration}", "-q"]

    if display_filter:
        cmd += ["-Y", display_filter]

    if mode == "protocol":
        cmd += ["-z", "io,phs"]
    elif mode == "endpoint":
        cmd += ["-z", "endpoints,ip"]
    elif mode == "port":
        cmd += ["-z", "endpoints,tcp"]
    else:  # overview
        cmd += ["-z", f"io,stat,{interval}"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 30)
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "统计超时", -1
    except FileNotFoundError:
        return "", "tshark 未找到", -2


def parse_io_stat(stdout):
    """解析 io,stat 输出。"""
    intervals = []
    for line in stdout.splitlines():
        m = re.match(r"\|\s*([\d.]+)\s*<>\s*([\d.]+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", line)
        if m:
            intervals.append({
                "start": float(m.group(1)),
                "end": float(m.group(2)),
                "frames": int(m.group(3)),
                "bytes": int(m.group(4)),
            })
    return intervals


def parse_protocol_hierarchy(stdout):
    """解析 io,phs 输出。"""
    protocols = []
    for line in stdout.splitlines():
        line = line.strip()
        m = re.match(r"(\S+)\s+frames:(\d+)\s+bytes:(\d+)", line)
        if m:
            protocols.append({
                "protocol": m.group(1),
                "frames": int(m.group(2)),
                "bytes": int(m.group(3)),
            })
    return protocols


def parse_endpoints(stdout):
    """解析 endpoints 输出。"""
    endpoints = []
    started = False
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("=") or "Filter:" in line:
            started = True
            continue
        if not started or not line or "Address" in line:
            continue
        parts = re.split(r"\s+", line)
        if len(parts) >= 3:
            endpoints.append({
                "address": parts[0],
                "packets": parts[1],
                "bytes": parts[2],
                "raw": line,
            })
    return endpoints


def main():
    parser = argparse.ArgumentParser(description="流量统计")
    parser.add_argument("--interval", type=int, default=1, help="统计间隔(秒)")
    parser.add_argument("--mode", default="overview",
                        choices=["overview", "protocol", "endpoint", "port"])
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON 输出")
    args = parser.parse_args()

    config = load_config()
    exe = config.get("tshark_exe", "tshark")
    iface = config.get("default_interface", "")
    duration = config.get("default_duration", 30)
    display_filter = config.get("default_display_filter", "")

    if not check_tshark(exe):
        error = {
            "status": "error",
            "action": "stats",
            "error": {
                "code": "tshark_not_found",
                "message": f"未找到 tshark ({exe})，请确认 Wireshark 已安装且已加入 PATH",
            },
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        sys.exit(1)

    if not iface:
        error = {
            "status": "error",
            "action": "stats",
            "error": {"code": "no_interface", "message": "config.json 中未配置 default_interface"},
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        sys.exit(1)

    print(f"[net stats] 接口={iface}, 时长={duration}s, 模式={args.mode}", file=sys.stderr)

    stdout, stderr, rc = run_tshark_stats(exe, iface, duration, args.mode, args.interval, display_filter)

    if rc != 0:
        error = {
            "status": "error",
            "action": "stats",
            "error": {"code": "stats_failed", "message": stderr.strip() or "统计失败"},
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        sys.exit(1)

    result = {
        "status": "ok",
        "action": "stats",
        "summary": {"duration_sec": duration, "mode": args.mode},
        "details": {},
    }

    if args.mode == "overview":
        intervals = parse_io_stat(stdout)
        total_frames = sum(i["frames"] for i in intervals)
        total_bytes = sum(i["bytes"] for i in intervals)
        result["summary"]["description"] = f"{duration}s 内共 {total_frames} 帧, {total_bytes} 字节"
        result["details"]["intervals"] = intervals
        result["details"]["total_frames"] = total_frames
        result["details"]["total_bytes"] = total_bytes

    elif args.mode == "protocol":
        protocols = parse_protocol_hierarchy(stdout)
        result["summary"]["description"] = f"检测到 {len(protocols)} 种协议"
        result["details"]["protocols"] = protocols

    elif args.mode in ("endpoint", "port"):
        endpoints = parse_endpoints(stdout)
        result["summary"]["description"] = f"发现 {len(endpoints)} 个端点"
        result["details"]["endpoints"] = endpoints

    if args.output_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[net stats] {result['summary'].get('description', '统计完成')}")
        details = result["details"]
        if "intervals" in details:
            print(f"\n  {'时间段':<20} {'帧数':<10} {'字节数'}")
            for i in details["intervals"]:
                print(f"  {i['start']:.0f}-{i['end']:.0f}s{'':<14} {i['frames']:<10} {i['bytes']}")
        if "protocols" in details:
            print("\n  协议分布:")
            for p in details["protocols"][:15]:
                print(f"    {p['protocol']}: {p['frames']} frames, {p['bytes']} bytes")
        if "endpoints" in details:
            print("\n  端点:")
            for e in details["endpoints"][:15]:
                print(f"    {e['address']}: {e['packets']} pkts, {e['bytes']} bytes")


if __name__ == "__main__":
    main()
