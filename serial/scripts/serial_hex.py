"""串口 Hex Dump 查看"""

import argparse
import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from serial_runtime import (
    get_serial_config,
    get_mux_info,
    open_serial_port,
    save_project_config,
    update_state_entry,
)

PARITY_MAP = {"none": "N", "even": "E", "odd": "O", "mark": "M", "space": "S"}
IDLE_FLUSH_SEC = 0.2


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


def emit_chunk(data, offset, width, show_ascii, use_json):
    now = datetime.now().isoformat(timespec="milliseconds")
    if use_json:
        ascii_str = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in data)
        output_json({
            "timestamp": now,
            "offset": offset,
            "length": len(data),
            "hex": data.hex(" "),
            "ascii": ascii_str,
        })
    else:
        print(hex_dump_line(data, offset, width, show_ascii))


def main():
    parser = argparse.ArgumentParser(description="串口 Hex Dump 查看")
    parser.add_argument("--port", help="串口号 (如 COM3)")
    parser.add_argument("--baudrate", type=int, help="波特率")
    parser.add_argument("--bytesize", type=int, help="数据位")
    parser.add_argument("--parity", help="校验位 (none/even/odd)")
    parser.add_argument("--stopbits", type=int, help="停止位")
    parser.add_argument("--encoding", help="编码")
    parser.add_argument("--width", type=int, default=16, help="每行字节数")
    parser.add_argument("--timeout", type=float, default=0, help="超时秒数，0=无限")
    parser.add_argument("--no-ascii", action="store_true", help="不显示 ASCII 列")
    parser.add_argument("--direct", action="store_true", help="直连真实串口，跳过 mux")
    parser.add_argument("--json", action="store_true", help="JSON Lines 输出")
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

    try:
        use_mux = not args.direct
        ser = open_serial_port(cfg, use_mux=use_mux)
        if use_mux and get_mux_info():
            print("[mux] 已通过多路复用连接，请避免在 minicom 中同时写入以免串口数据冲突", file=sys.stderr)
        ser.timeout = 0.1
    except Exception as e:
        error_exit("connect_failed", str(e), args.json)

    total_bytes = 0
    offset = 0
    running = True
    show_ascii = not args.no_ascii
    buffer = bytearray()
    last_data_at = 0.0

    def on_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        while running:
            if args.timeout > 0 and (time.time() - start_time) >= args.timeout:
                break

            read_size = max(1, getattr(ser, "in_waiting", 0) or 1)
            data = ser.read(read_size)
            if not data:
                if buffer and last_data_at and (time.time() - last_data_at) >= IDLE_FLUSH_SEC:
                    chunk = bytes(buffer)
                    emit_chunk(chunk, offset, args.width, show_ascii, args.json)
                    total_bytes += len(chunk)
                    offset += len(chunk)
                    buffer.clear()
                continue

            buffer.extend(data)
            last_data_at = time.time()

            while len(buffer) >= args.width:
                chunk = bytes(buffer[:args.width])
                del buffer[:args.width]
                emit_chunk(chunk, offset, args.width, show_ascii, args.json)
                total_bytes += len(chunk)
                offset += len(chunk)

    except Exception as e:
        error_exit("read_error", str(e), args.json)
    finally:
        if buffer:
            chunk = bytes(buffer)
            emit_chunk(chunk, offset, args.width, show_ascii, args.json)
            total_bytes += len(chunk)
        ser.close()

    duration = round(time.time() - start_time, 1)
    summary = f"Hex 查看结束，共 {total_bytes} 字节，耗时 {duration}s\n"
    sys.stderr.buffer.write(summary.encode("utf-8"))
    sys.stderr.buffer.flush()

    # 更新状态
    update_state_entry("last_observe", {
        "type": "serial_hex",
        "port": cfg["port"],
        "baudrate": cfg["baudrate"],
        "bytes_received": total_bytes,
        "duration_sec": duration,
    })


if __name__ == "__main__":
    main()
