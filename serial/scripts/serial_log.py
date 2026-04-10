"""串口日志记录"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

PARITY_MAP = {"none": "N", "even": "E", "odd": "O", "mark": "M", "space": "S"}


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def open_serial(cfg):
    import serial

    parity = PARITY_MAP.get(cfg.get("default_parity", "none"), "N")
    return serial.Serial(
        port=cfg["default_port"],
        baudrate=cfg.get("default_baudrate", 115200),
        bytesize=cfg.get("default_bytesize", 8),
        parity=parity,
        stopbits=cfg.get("default_stopbits", 1),
        timeout=cfg.get("default_timeout_sec", 1.0),
    )


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
    parser.add_argument("--output", "-o", help="输出文件路径")
    parser.add_argument("--timestamp", action="store_true", help="每行加时间戳")
    parser.add_argument("--max-size", type=float, default=0, help="最大文件大小(MB)，0=无限")
    parser.add_argument("--duration", type=float, default=0, help="记录时长(秒)，0=无限")
    parser.add_argument("--format", choices=["text", "csv", "json"], default="text", help="输出格式")
    parser.add_argument("--console", action="store_true", help="同时输出到控制台(stderr)")
    parser.add_argument("--json", action="store_true", help="最终输出 summary JSON")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.get("default_port"):
        error_exit("no_port", "config.json 中未配置 default_port", args.json)
    if not cfg.get("default_baudrate"):
        error_exit("no_baudrate", "config.json 中未配置 default_baudrate", args.json)

    log_dir = cfg.get("default_log_dir", ".logs")
    if not args.output:
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = {"text": "log", "csv": "csv", "json": "jsonl"}[args.format]
        args.output = os.path.join(log_dir, f"serial_{ts}.{ext}")

    try:
        ser = open_serial(cfg)
    except Exception as e:
        error_exit("connect_failed", str(e), args.json)

    line_count = 0
    byte_count = 0
    start_time = time.time()
    running = True
    encoding = cfg.get("default_encoding", "utf-8")
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


if __name__ == "__main__":
    main()
