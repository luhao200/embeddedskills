"""串口日志记录"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from serial_runtime import (
    get_serial_config,
    open_serial_port,
    save_project_config,
    update_state_entry,
    normalize_path,
)

PARITY_MAP = {"none": "N", "even": "E", "odd": "O", "mark": "M", "space": "S"}


def output_json(obj):
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def error_exit(code, message, use_json):
    result = {"status": "error", "action": "log", "error": {"code": code, "message": message}}
    if use_json:
        output_json(result)
    else:
        print(f"错误: {message}", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="串口日志记录")
    parser.add_argument("--port", help="串口号 (如 COM3)")
    parser.add_argument("--baudrate", type=int, help="波特率")
    parser.add_argument("--bytesize", type=int, help="数据位")
    parser.add_argument("--parity", help="校验位 (none/even/odd)")
    parser.add_argument("--stopbits", type=int, help="停止位")
    parser.add_argument("--encoding", help="编码")
    parser.add_argument("--output", "-o", help="输出文件路径")
    parser.add_argument("--timestamp", action="store_true", help="每行加时间戳")
    parser.add_argument("--max-size", type=float, default=0, help="最大文件大小(MB)，0=无限")
    parser.add_argument("--duration", type=float, default=0, help="记录时长(秒)，0=无限")
    parser.add_argument("--format", choices=["text", "csv", "json"], default="text", help="输出格式")
    parser.add_argument("--console", action="store_true", help="同时输出到控制台(stderr)")
    parser.add_argument("--direct", action="store_true", help="直连真实串口，跳过 mux")
    parser.add_argument("--json", action="store_true", help="最终输出 summary JSON")
    args = parser.parse_args()

    start_time = time.time()

    # 获取配置
    cfg, sources = get_serial_config(
        cli_port=args.port,
        cli_baudrate=args.baudrate,
        cli_bytesize=args.bytesize,
        cli_parity=args.parity,
        cli_stopbits=args.stopbits,
        cli_encoding=args.encoding,
    )

    if cfg is None:
        if sources.get("need_selection"):
            error_exit("multiple_candidates", f"{sources['error']}，请用 --port 指定", args.json)
        else:
            error_exit("config_error", sources.get("error", "配置错误"), args.json)

    # 保存确认的配置
    save_project_config(values={
        "port": cfg["port"],
        "baudrate": cfg["baudrate"],
        "bytesize": cfg["bytesize"],
        "parity": cfg["parity"],
        "stopbits": cfg["stopbits"],
        "encoding": cfg["encoding"],
    })

    log_dir = cfg["log_dir"]
    if not args.output:
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        ext = {"text": "log", "csv": "csv", "json": "jsonl"}[args.format]
        args.output = os.path.join(log_dir, f"serial_{ts}.{ext}")

    try:
        use_mux = not args.direct
        ser = open_serial_port(cfg, use_mux=use_mux)
        if getattr(ser, "_serial_skill_using_mux", False):
            print("[mux] 已通过多路复用连接，请避免在 minicom 中同时写入以免串口数据冲突", file=sys.stderr)
    except Exception as e:
        error_exit("connect_failed", str(e), args.json)

    line_count = 0
    byte_count = 0
    running = True
    encoding = cfg["encoding"]
    max_bytes = int(args.max_size * 1024 * 1024) if args.max_size > 0 else 0

    def on_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    try:
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            if args.format == "csv":
                f.write("timestamp,text\n")

            while running:
                if args.duration > 0 and (time.time() - start_time) >= args.duration:
                    break
                if max_bytes > 0 and byte_count >= max_bytes:
                    break

                raw = ser.readline()
                if not raw:
                    continue

                try:
                    text = raw.decode(encoding, errors="replace").rstrip("\r\n")
                except Exception:
                    text = raw.hex()

                now = datetime.now().isoformat(timespec="milliseconds")
                line_count += 1

                if args.format == "text":
                    line = f"[{now}] {text}\n" if args.timestamp else f"{text}\n"
                elif args.format == "csv":
                    escaped = text.replace('"', '""')
                    line = f'{now},"{escaped}"\n'
                else:
                    line = json.dumps({"timestamp": now, "text": text}, ensure_ascii=False) + "\n"

                f.write(line)
                byte_count += len(line.encode("utf-8"))

                if args.console:
                    prefix = f"[{now}] " if args.timestamp else ""
                    print(f"{prefix}{text}", file=sys.stderr)

    except Exception as e:
        error_exit("write_error", str(e), args.json)
    finally:
        ser.close()

    duration = round(time.time() - start_time, 1)
    result = {
        "status": "ok",
        "action": "log",
        "summary": {
            "file": os.path.abspath(args.output),
            "lines": line_count,
            "bytes": byte_count,
            "duration_sec": duration,
            "format": args.format,
        },
    }

    if args.json:
        output_json(result)
    else:
        print(f"\n日志已保存: {os.path.abspath(args.output)}")
        print(f"  共 {line_count} 行, {byte_count} 字节, 耗时 {duration}s")

    # 更新状态
    update_state_entry("last_observe", {
        "type": "serial_log",
        "port": cfg["port"],
        "baudrate": cfg["baudrate"],
        "file": os.path.abspath(args.output),
        "lines": line_count,
        "duration_sec": duration,
    })


if __name__ == "__main__":
    main()
