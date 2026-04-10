"""串口实时文本监控"""

import argparse
import json
import re
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
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def error_exit(action, code, message, use_json):
    result = {"status": "error", "action": action, "error": {"code": code, "message": message}}
    if use_json:
        output_json(result)
    else:
        print(f"错误: {message}", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="串口实时文本监控")
    parser.add_argument("--timestamp", action="store_true", help="显示时间戳")
    parser.add_argument("--filter", help="正则过滤（仅显示匹配行）")
    parser.add_argument("--exclude", help="正则排除（隐藏匹配行）")
    parser.add_argument("--timeout", type=float, default=0, help="超时秒数，0=无限")
    parser.add_argument("--json", action="store_true", help="JSON Lines 输出")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.get("default_port"):
        error_exit("monitor", "no_port", "config.json 中未配置 default_port", args.json)
    if not cfg.get("default_baudrate"):
        error_exit("monitor", "no_baudrate", "config.json 中未配置 default_baudrate", args.json)

    include_re = None
    exclude_re = None
    if args.filter:
        try:
            include_re = re.compile(args.filter)
        except re.error:
            error_exit("monitor", "bad_regex", f"无效正则: {args.filter}", args.json)
    if args.exclude:
        try:
            exclude_re = re.compile(args.exclude)
        except re.error:
            error_exit("monitor", "bad_regex", f"无效正则: {args.exclude}", args.json)

    try:
        ser = open_serial(cfg)
    except Exception as e:
        error_exit("monitor", "connect_failed", str(e), args.json)

    line_count = 0
    start_time = time.time()
    running = True

    def on_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    encoding = cfg.get("default_encoding", "utf-8")

    try:
        while running:
            if args.timeout > 0 and (time.time() - start_time) >= args.timeout:
                break

            raw = ser.readline()
            if not raw:
                continue

            try:
                text = raw.decode(encoding, errors="replace").rstrip("\r\n")
            except Exception:
                text = raw.hex()

            if include_re:
                try:
                    if not include_re.search(text):
                        continue
                except Exception:
                    pass

            if exclude_re:
                try:
                    if exclude_re.search(text):
                        continue
                except Exception:
                    pass

            line_count += 1
            now = datetime.now().isoformat(timespec="milliseconds")

            if args.json:
                output_json({"timestamp": now, "port": cfg["default_port"],
                             "baudrate": cfg.get("default_baudrate"), "text": text})
            else:
                prefix = f"[{now}] " if args.timestamp else ""
                print(f"{prefix}{text}")

    except Exception as e:
        error_exit("monitor", "read_error", str(e), args.json)
    finally:
        ser.close()

    duration = round(time.time() - start_time, 1)
    summary = f"监控结束，共 {line_count} 行，耗时 {duration}s\n"
    sys.stderr.buffer.write(summary.encode("utf-8"))
    sys.stderr.buffer.flush()


if __name__ == "__main__":
    main()
