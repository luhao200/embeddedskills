"""串口 Hex Dump 查看"""

import argparse
import json
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


def error_exit(code, message, use_json):
    result = {"status": "error", "action": "hex", "error": {"code": code, "message": message}}
    if use_json:
        output_json(result)
    else:
        print(f"错误: {message}", file=sys.stderr)
    sys.exit(1)


def hex_dump_line(data, offset, width, show_ascii):
    """格式化一行 hex dump"""
    hex_part = " ".join(f"{b:02X}" for b in data)
    hex_part = hex_part.ljust(width * 3 - 1)
    line = f"{offset:08X}  {hex_part}"
    if show_ascii:
        ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in data)
        line += f"  |{ascii_part}|"
    return line


def main():
    parser = argparse.ArgumentParser(description="串口 Hex Dump 查看")
    parser.add_argument("--width", type=int, default=16, help="每行字节数")
    parser.add_argument("--timeout", type=float, default=0, help="超时秒数，0=无限")
    parser.add_argument("--no-ascii", action="store_true", help="不显示 ASCII 列")
    parser.add_argument("--json", action="store_true", help="JSON Lines 输出")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.get("default_port"):
        error_exit("no_port", "config.json 中未配置 default_port", args.json)
    if not cfg.get("default_baudrate"):
        error_exit("no_baudrate", "config.json 中未配置 default_baudrate", args.json)

    try:
        ser = open_serial(cfg)
    except Exception as e:
        error_exit("connect_failed", str(e), args.json)

    total_bytes = 0
    offset = 0
    start_time = time.time()
    running = True
    show_ascii = not args.no_ascii

    def on_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        while running:
            if args.timeout > 0 and (time.time() - start_time) >= args.timeout:
                break

            data = ser.read(args.width)
            if not data:
                continue

            total_bytes += len(data)
            now = datetime.now().isoformat(timespec="milliseconds")

            if args.json:
                ascii_str = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in data)
                output_json({
                    "timestamp": now,
                    "offset": offset,
                    "length": len(data),
                    "hex": data.hex(" "),
                    "ascii": ascii_str,
                })
            else:
                print(hex_dump_line(data, offset, args.width, show_ascii))

            offset += len(data)

    except Exception as e:
        error_exit("read_error", str(e), args.json)
    finally:
        ser.close()

    duration = round(time.time() - start_time, 1)
    summary = f"Hex 查看结束，共 {total_bytes} 字节，耗时 {duration}s\n"
    sys.stderr.buffer.write(summary.encode("utf-8"))
    sys.stderr.buffer.flush()


if __name__ == "__main__":
    main()
