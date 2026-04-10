#!/usr/bin/env python3
"""网络接口发现工具，可关联 tshark 抓包接口列表。"""

import argparse
import io
import json
import re
import subprocess
import sys

# 确保 stdout 使用 UTF-8 编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def parse_ipconfig():
    """解析 ipconfig /all 获取网络接口信息。"""
    try:
        result = subprocess.run(
            ["ipconfig", "/all"], capture_output=True, text=True, encoding="gbk", errors="replace"
        )
    except FileNotFoundError:
        return []

    interfaces = []
    current = None

    for line in result.stdout.splitlines():
        # 适配器标题行
        adapter_match = re.match(r"^(\S.*?)\s*适配器\s+(.+?)\s*[:：]", line)
        if not adapter_match:
            adapter_match = re.match(r"^(\S.*?)\s+adapter\s+(.+?)\s*[:：]", line, re.IGNORECASE)
        if adapter_match:
            if current:
                interfaces.append(current)
            current = {
                "type": adapter_match.group(1).strip(),
                "name": adapter_match.group(2).strip(),
                "description": "",
                "mac": "",
                "ipv4": "",
                "subnet": "",
                "gateway": "",
                "dhcp": "",
                "status": "up",
            }
            continue

        if current is None:
            continue

        line_stripped = line.strip()

        if re.match(r"(媒体状态|Media State)", line_stripped, re.IGNORECASE):
            if "断开" in line_stripped or "disconnected" in line_stripped.lower():
                current["status"] = "down"
        elif re.match(r"(描述|Description)", line_stripped, re.IGNORECASE):
            current["description"] = line_stripped.split(":", 1)[-1].strip() if ":" in line_stripped else ""
        elif re.match(r"(物理地址|Physical Address)", line_stripped, re.IGNORECASE):
            current["mac"] = line_stripped.split(":", 1)[-1].strip() if ":" in line_stripped else ""
        elif re.match(r"(IPv4 地址|IPv4 Address)", line_stripped, re.IGNORECASE):
            val = line_stripped.split(":", 1)[-1].strip() if ":" in line_stripped else ""
            current["ipv4"] = re.sub(r"\(.*?\)", "", val).strip()
        elif re.match(r"(子网掩码|Subnet Mask)", line_stripped, re.IGNORECASE):
            current["subnet"] = line_stripped.split(":", 1)[-1].strip() if ":" in line_stripped else ""
        elif re.match(r"(默认网关|Default Gateway)", line_stripped, re.IGNORECASE):
            current["gateway"] = line_stripped.split(":", 1)[-1].strip() if ":" in line_stripped else ""
        elif re.match(r"DHCP", line_stripped, re.IGNORECASE) and "已启用" in line_stripped or "Yes" in line_stripped:
            current["dhcp"] = "enabled"

    if current:
        interfaces.append(current)

    return interfaces


def parse_tshark_interfaces(tshark_exe="tshark"):
    """解析 tshark -D 获取抓包接口列表。"""
    try:
        result = subprocess.run(
            [tshark_exe, "-D"], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    interfaces = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # 格式: 1. \Device\NPF_{...} (描述)
        m = re.match(r"(\d+)\.\s+(.+?)(?:\s+\((.+?)\))?\s*$", line)
        if m:
            interfaces.append({
                "index": int(m.group(1)),
                "device": m.group(2).strip(),
                "description": m.group(3).strip() if m.group(3) else "",
            })
    return interfaces


def main():
    parser = argparse.ArgumentParser(description="列出网络接口")
    parser.add_argument("--filter", default="", help="按关键词筛选接口")
    parser.add_argument("--tshark", action="store_true", help="同时显示 tshark 抓包接口")
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON 输出")
    parser.add_argument("--tshark-exe", default="tshark", help="tshark 路径")
    args = parser.parse_args()

    interfaces = parse_ipconfig()

    if args.filter:
        kw = args.filter.lower()
        interfaces = [
            iface for iface in interfaces
            if kw in iface["name"].lower()
            or kw in iface["description"].lower()
            or kw in iface["type"].lower()
            or kw in iface.get("ipv4", "").lower()
        ]

    result = {
        "status": "ok",
        "action": "iface",
        "summary": f"发现 {len(interfaces)} 个网络接口",
        "details": {
            "interfaces": interfaces,
        },
    }

    if args.tshark:
        tshark_ifaces = parse_tshark_interfaces(args.tshark_exe)
        if tshark_ifaces is None:
            result["details"]["tshark_interfaces"] = []
            result["details"]["tshark_note"] = "tshark 不可用或未找到"
        else:
            result["details"]["tshark_interfaces"] = tshark_ifaces

    if args.output_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[net iface] {result['summary']}")
        for iface in interfaces:
            status_icon = "●" if iface["status"] == "up" else "○"
            print(f"  {status_icon} {iface['name']} ({iface['type']})")
            if iface["description"]:
                print(f"    描述: {iface['description']}")
            if iface["ipv4"]:
                print(f"    IPv4: {iface['ipv4']}/{iface['subnet']}")
            if iface["mac"]:
                print(f"    MAC:  {iface['mac']}")
            if iface["gateway"]:
                print(f"    网关: {iface['gateway']}")
        if args.tshark and result["details"].get("tshark_interfaces"):
            print("\n[tshark 抓包接口]")
            for ti in result["details"]["tshark_interfaces"]:
                print(f"  {ti['index']}. {ti['device']}")
                if ti["description"]:
                    print(f"     {ti['description']}")


if __name__ == "__main__":
    main()
