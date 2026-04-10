#!/usr/bin/env python3
"""基于 tshark/capinfos 的 pcap 离线分析工具。"""

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


def run_cmd(cmd, timeout=30):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
        return result.stdout, result.stderr, result.returncode
    except FileNotFoundError:
        return "", f"命令未找到: {cmd[0]}", -1
    except subprocess.TimeoutExpired:
        return "", "命令超时", -2


def get_capinfos_summary(capinfos_exe, pcap_file):
    """通过 capinfos 获取文件级统计。"""
    stdout, stderr, rc = run_cmd([capinfos_exe, "-M", pcap_file])
    if rc != 0:
        return None

    info = {}
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if "number of packets" in key:
            info["packet_count"] = int(val) if val.isdigit() else val
        elif "capture duration" in key:
            info["duration"] = val
        elif "file size" in key:
            info["file_size"] = val
        elif "data size" in key:
            info["data_size"] = val
        elif "first packet time" in key:
            info["first_packet"] = val
        elif "last packet time" in key:
            info["last_packet"] = val
        elif "average packet size" in key:
            info["avg_packet_size"] = val
        elif "data byte rate" in key:
            info["byte_rate"] = val
    return info


def get_protocol_hierarchy(tshark_exe, pcap_file, display_filter="", decode_as=""):
    """获取协议层次统计。"""
    cmd = [tshark_exe, "-r", pcap_file, "-q", "-z", "io,phs"]
    if display_filter:
        cmd += ["-Y", display_filter]
    if decode_as:
        cmd += ["-d", decode_as]

    stdout, _, rc = run_cmd(cmd, timeout=60)
    if rc != 0:
        return []

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


def get_conversations(tshark_exe, pcap_file, display_filter="", decode_as="", top=20):
    """获取会话统计。"""
    cmd = [tshark_exe, "-r", pcap_file, "-q", "-z", "conv,ip"]
    if display_filter:
        cmd += ["-Y", display_filter]
    if decode_as:
        cmd += ["-d", decode_as]

    stdout, _, rc = run_cmd(cmd, timeout=60)
    if rc != 0:
        return []

    conversations = []
    header_found = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("="):
            header_found = True
            continue
        if not header_found:
            continue
        # 格式: addr_a <-> addr_b  frames_a bytes_a frames_b bytes_b frames_total bytes_total ...
        parts = re.split(r"\s+", line)
        if len(parts) >= 8 and "<->" in parts:
            idx = parts.index("<->")
            if idx >= 1 and idx + 1 < len(parts):
                conversations.append({
                    "addr_a": parts[idx - 1],
                    "addr_b": parts[idx + 1],
                    "raw": line,
                })
    return conversations[:top]


def get_endpoints(tshark_exe, pcap_file, display_filter="", decode_as="", top=20):
    """获取端点统计。"""
    cmd = [tshark_exe, "-r", pcap_file, "-q", "-z", "endpoints,ip"]
    if display_filter:
        cmd += ["-Y", display_filter]
    if decode_as:
        cmd += ["-d", decode_as]

    stdout, _, rc = run_cmd(cmd, timeout=60)
    if rc != 0:
        return []

    endpoints = []
    header_found = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("="):
            header_found = True
            continue
        if not header_found or "Address" in line:
            continue
        parts = re.split(r"\s+", line)
        if len(parts) >= 3:
            endpoints.append({
                "address": parts[0],
                "packets": parts[1] if len(parts) > 1 else "",
                "bytes": parts[2] if len(parts) > 2 else "",
                "raw": line,
            })
    return endpoints[:top]


def detect_anomalies(tshark_exe, pcap_file, display_filter="", decode_as=""):
    """检测常见网络异常（重传、RST、错误等）。"""
    checks = [
        ("tcp.analysis.retransmission", "TCP 重传"),
        ("tcp.analysis.fast_retransmission", "TCP 快速重传"),
        ("tcp.analysis.duplicate_ack", "TCP 重复 ACK"),
        ("tcp.flags.reset==1", "TCP RST"),
        ("icmp.type==3", "ICMP 不可达"),
        ("dns.flags.rcode!=0", "DNS 错误"),
        ("tcp.analysis.zero_window", "TCP 零窗口"),
    ]

    anomalies = []
    for filt, desc in checks:
        combined = f"({filt})"
        if display_filter:
            combined = f"({display_filter}) && ({filt})"

        cmd = [tshark_exe, "-r", pcap_file, "-Y", combined, "-T", "fields", "-e", "frame.number"]
        if decode_as:
            cmd += ["-d", decode_as]

        stdout, _, rc = run_cmd(cmd, timeout=30)
        if rc == 0:
            count = len([l for l in stdout.strip().splitlines() if l.strip()])
            if count > 0:
                anomalies.append({"type": desc, "filter": filt, "count": count})

    return anomalies


def get_io_stats(tshark_exe, pcap_file, display_filter="", decode_as=""):
    """获取 IO 统计。"""
    cmd = [tshark_exe, "-r", pcap_file, "-q", "-z", "io,stat,1"]
    if display_filter:
        cmd += ["-Y", display_filter]
    if decode_as:
        cmd += ["-d", decode_as]

    stdout, _, rc = run_cmd(cmd, timeout=60)
    if rc != 0:
        return []

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


def main():
    parser = argparse.ArgumentParser(description="分析 pcap 文件")
    parser.add_argument("pcap_file", help="pcap/pcapng 文件路径")
    parser.add_argument("--mode", default="summary",
                        choices=["summary", "protocols", "conversations", "endpoints", "io", "anomalies", "all"])
    parser.add_argument("--filter", default="", help="显示过滤器")
    parser.add_argument("--top", type=int, default=20, help="显示前 N 条")
    parser.add_argument("--decode-as", default="", help="解码规则")
    parser.add_argument("--export-fields", default="", help="导出字段列表")
    parser.add_argument("--output", default="", help="CSV 输出路径")
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON 输出")
    args = parser.parse_args()

    if not os.path.exists(args.pcap_file):
        error = {
            "status": "error",
            "action": "analyze",
            "error": {"code": "file_not_found", "message": f"文件不存在: {args.pcap_file}"},
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        sys.exit(1)

    config = load_config()
    tshark_exe = config.get("tshark_exe", "tshark")
    capinfos_exe = config.get("capinfos_exe", "capinfos")

    result = {
        "status": "ok",
        "action": "analyze",
        "summary": "",
        "details": {},
    }

    modes = [args.mode] if args.mode != "all" else [
        "summary", "protocols", "conversations", "endpoints", "io", "anomalies"
    ]

    for mode in modes:
        if mode == "summary":
            info = get_capinfos_summary(capinfos_exe, args.pcap_file)
            if info:
                result["details"]["summary"] = info
                result["summary"] = f"文件包含 {info.get('packet_count', '?')} 个数据包"
            else:
                # 回退用 tshark 统计
                stdout, _, rc = run_cmd([tshark_exe, "-r", args.pcap_file, "-q", "-z", "io,stat,0"])
                result["details"]["summary"] = {"raw": stdout}

        elif mode == "protocols":
            result["details"]["protocols"] = get_protocol_hierarchy(
                tshark_exe, args.pcap_file, args.filter, args.decode_as
            )

        elif mode == "conversations":
            result["details"]["conversations"] = get_conversations(
                tshark_exe, args.pcap_file, args.filter, args.decode_as, args.top
            )

        elif mode == "endpoints":
            result["details"]["endpoints"] = get_endpoints(
                tshark_exe, args.pcap_file, args.filter, args.decode_as, args.top
            )

        elif mode == "io":
            result["details"]["io_stats"] = get_io_stats(
                tshark_exe, args.pcap_file, args.filter, args.decode_as
            )

        elif mode == "anomalies":
            result["details"]["anomalies"] = detect_anomalies(
                tshark_exe, args.pcap_file, args.filter, args.decode_as
            )

    # 导出字段
    if args.export_fields and args.output:
        fields = [f.strip() for f in args.export_fields.split(",")]
        cmd = [tshark_exe, "-r", args.pcap_file, "-T", "fields"]
        for f in fields:
            cmd += ["-e", f]
        cmd += ["-E", "header=y", "-E", "separator=,"]
        if args.filter:
            cmd += ["-Y", args.filter]
        if args.decode_as:
            cmd += ["-d", args.decode_as]

        stdout, _, rc = run_cmd(cmd, timeout=120)
        if rc == 0:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(stdout)
            result["details"]["exported"] = {"file": args.output, "fields": fields}

    if args.output_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[net analyze] {result.get('summary', '分析完成')}")
        details = result["details"]
        if "summary" in details and isinstance(details["summary"], dict):
            for k, v in details["summary"].items():
                if k != "raw":
                    print(f"  {k}: {v}")
        if "protocols" in details:
            print("\n  协议统计:")
            for p in details["protocols"][:args.top]:
                print(f"    {p['protocol']}: {p['frames']} frames, {p['bytes']} bytes")
        if "anomalies" in details and details["anomalies"]:
            print("\n  异常检测:")
            for a in details["anomalies"]:
                print(f"    {a['type']}: {a['count']} 次")
        if "conversations" in details:
            print("\n  会话:")
            for c in details["conversations"][:5]:
                print(f"    {c['addr_a']} <-> {c['addr_b']}")
        if "endpoints" in details:
            print("\n  端点:")
            for e in details["endpoints"][:5]:
                print(f"    {e['address']}")


if __name__ == "__main__":
    main()
