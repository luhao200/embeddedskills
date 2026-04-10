#!/usr/bin/env python3
"""连通性测试工具，支持 ICMP/TCP ping、批量测试和路由追踪。"""

import argparse
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def icmp_ping(target, count=4, timeout_ms=1000):
    """使用系统 ping 命令做 ICMP 测试。"""
    timeout_sec = max(1, timeout_ms // 1000)
    cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), target]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="gbk",
                                errors="replace", timeout=count * timeout_sec + 10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"target": target, "reachable": False, "error": "ping 命令超时或不可用"}

    output = result.stdout
    reachable = False
    sent = received = 0
    avg_ms = None

    for line in output.splitlines():
        # 统计行
        m = re.search(r"已发送\s*=\s*(\d+).*已接收\s*=\s*(\d+)", line)
        if not m:
            m = re.search(r"Sent\s*=\s*(\d+).*Received\s*=\s*(\d+)", line, re.IGNORECASE)
        if m:
            sent = int(m.group(1))
            received = int(m.group(2))
            reachable = received > 0

        # 平均延迟
        m2 = re.search(r"平均\s*=\s*(\d+)ms", line)
        if not m2:
            m2 = re.search(r"Average\s*=\s*(\d+)ms", line, re.IGNORECASE)
        if m2:
            avg_ms = int(m2.group(1))

    return {
        "target": target,
        "reachable": reachable,
        "sent": sent,
        "received": received,
        "loss_rate": f"{((sent - received) / sent * 100):.0f}%" if sent > 0 else "N/A",
        "avg_ms": avg_ms,
    }


def tcp_ping(target, port, timeout_ms=1000):
    """TCP 连通性测试。"""
    timeout_sec = timeout_ms / 1000.0
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_sec)
        start = __import__("time").time()
        sock.connect((target, port))
        elapsed = (__import__("time").time() - start) * 1000
        sock.close()
        return {"target": target, "port": port, "reachable": True, "latency_ms": round(elapsed, 1)}
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return {"target": target, "port": port, "reachable": False, "error": str(e)}


def traceroute(target, timeout_ms=1000):
    """路由追踪。"""
    timeout_sec = max(1, timeout_ms // 1000)
    cmd = ["tracert", "-d", "-w", str(timeout_ms), "-h", "30", target]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="gbk",
                                errors="replace", timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"target": target, "hops": [], "error": "tracert 超时或不可用"}

    hops = []
    for line in result.stdout.splitlines():
        m = re.match(r"\s*(\d+)\s+(.+)", line)
        if m:
            hop_num = int(m.group(1))
            rest = m.group(2).strip()
            hops.append({"hop": hop_num, "detail": rest})

    return {"target": target, "hops": hops}


def main():
    parser = argparse.ArgumentParser(description="连通性测试")
    parser.add_argument("--tcp", type=int, default=0, help="TCP 端口")
    parser.add_argument("--count", type=int, default=4, help="ping 次数")
    parser.add_argument("--traceroute", action="store_true", help="路由追踪")
    parser.add_argument("--concurrent", type=int, default=4, help="并发线程数")
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON 输出")
    args = parser.parse_args()

    config = load_config()
    target = config.get("default_target", "")
    timeout_ms = config.get("default_timeout_ms", 1000)

    if not target:
        error = {
            "status": "error",
            "action": "ping",
            "error": {"code": "no_target", "message": "config.json 中未配置 default_target"},
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 支持逗号分隔的多目标
    targets = [t.strip() for t in target.split(",") if t.strip()]

    results = []

    if args.traceroute:
        for t in targets:
            results.append(traceroute(t, timeout_ms))
    elif args.tcp > 0:
        with ThreadPoolExecutor(max_workers=args.concurrent) as pool:
            futures = {pool.submit(tcp_ping, t, args.tcp, timeout_ms): t for t in targets}
            for future in as_completed(futures):
                results.append(future.result())
    else:
        with ThreadPoolExecutor(max_workers=args.concurrent) as pool:
            futures = {pool.submit(icmp_ping, t, args.count, timeout_ms): t for t in targets}
            for future in as_completed(futures):
                results.append(future.result())

    reachable_count = sum(1 for r in results if r.get("reachable", False))
    total = len(results)
    action = "traceroute" if args.traceroute else ("tcp_ping" if args.tcp > 0 else "ping")

    output = {
        "status": "ok",
        "action": action,
        "summary": {
            "total": total,
            "reachable": reachable_count,
            "description": f"{reachable_count}/{total} 目标可达",
        },
        "details": {"results": results},
    }

    if args.output_json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"[net {action}] {output['summary']['description']}")
        for r in results:
            if args.traceroute:
                print(f"\n  追踪 {r['target']}:")
                for h in r.get("hops", []):
                    print(f"    {h['hop']:>3}  {h['detail']}")
            else:
                icon = "+" if r.get("reachable") else "x"
                line = f"  [{icon}] {r['target']}"
                if r.get("port"):
                    line += f":{r['port']}"
                if r.get("avg_ms") is not None:
                    line += f"  延迟={r['avg_ms']}ms"
                elif r.get("latency_ms") is not None:
                    line += f"  延迟={r['latency_ms']}ms"
                if r.get("loss_rate"):
                    line += f"  丢包={r['loss_rate']}"
                if r.get("error"):
                    line += f"  ({r['error']})"
                print(line)


if __name__ == "__main__":
    main()
