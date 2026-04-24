"""串口实时文本监控"""

import argparse
import json
import re
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
    make_timing,
)

PARITY_MAP = {"none": "N", "even": "E", "odd": "O", "mark": "M", "space": "S"}
IDLE_FLUSH_SEC = 0.2


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


def emit_line(text, cfg, args, include_re, exclude_re):
    if include_re:
        try:
            if not include_re.search(text):
                return False
        except Exception:
            pass

    if exclude_re:
        try:
            if exclude_re.search(text):
                return False
        except Exception:
            pass

    now = datetime.now().isoformat(timespec="milliseconds")
    if args.json:
        output_json({"timestamp": now, "port": cfg["port"], "baudrate": cfg["baudrate"], "text": text})
    else:
        prefix = f"[{now}] " if args.timestamp else ""
        print(f"{prefix}{text}")
    return True


def main():
    parser = argparse.ArgumentParser(description="串口实时文本监控")
    parser.add_argument("--port", help="串口号 (如 COM3)")
    parser.add_argument("--baudrate", type=int, help="波特率")
    parser.add_argument("--bytesize", type=int, help="数据位")
    parser.add_argument("--parity", help="校验位 (none/even/odd)")
    parser.add_argument("--stopbits", type=int, help="停止位")
    parser.add_argument("--encoding", help="编码")
    parser.add_argument("--timestamp", action="store_true", help="显示时间戳")
    parser.add_argument("--filter", help="正则过滤（仅显示匹配行）")
    parser.add_argument("--exclude", help="正则排除（隐藏匹配行）")
    parser.add_argument("--timeout", type=float, default=0, help="超时秒数，0=无限")
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
            # 多候选情况
            error_exit("monitor", "multiple_candidates", f"{sources['error']}，请用 --port 指定", args.json)
        else:
            error_exit("monitor", "config_error", sources.get("error", "配置错误"), args.json)

    # 保存确认的配置
    save_project_config(values={
        "port": cfg["port"],
        "baudrate": cfg["baudrate"],
        "bytesize": cfg["bytesize"],
        "parity": cfg["parity"],
        "stopbits": cfg["stopbits"],
        "encoding": cfg["encoding"],
    })

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
        use_mux = not args.direct
        ser = open_serial_port(cfg, use_mux=use_mux)
        if getattr(ser, "_serial_skill_using_mux", False):
            print("[mux] 已通过多路复用连接，请避免在 minicom 中同时写入以免串口数据冲突", file=sys.stderr)
        ser.timeout = 0.1
    except Exception as e:
        error_exit("monitor", "connect_failed", str(e), args.json)

    line_count = 0
    running = True

    def on_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    encoding = cfg.get("encoding", "utf-8")
    text_buffer = ""
    last_data_at = 0.0
    skip_leading_lf = False

    try:
        while running:
            if args.timeout > 0 and (time.time() - start_time) >= args.timeout:
                break

            read_size = max(1, getattr(ser, "in_waiting", 0) or 1)
            raw = ser.read(read_size)
            if not raw:
                if text_buffer and last_data_at and (time.time() - last_data_at) >= IDLE_FLUSH_SEC:
                    if emit_line(text_buffer, cfg, args, include_re, exclude_re):
                        line_count += 1
                    text_buffer = ""
                continue

            try:
                chunk = raw.decode(encoding, errors="replace")
            except Exception:
                chunk = raw.hex()
            if skip_leading_lf and chunk.startswith("\n"):
                chunk = chunk[1:]
            skip_leading_lf = chunk.endswith("\r")
            text_buffer += chunk.replace("\r\n", "\n").replace("\r", "\n")
            last_data_at = time.time()

            parts = text_buffer.split("\n")
            if text_buffer.endswith("\n"):
                complete_lines = parts[:-1]
                text_buffer = ""
            else:
                complete_lines = parts[:-1]
                text_buffer = parts[-1]

            for text in complete_lines:
                if emit_line(text, cfg, args, include_re, exclude_re):
                    line_count += 1

    except Exception as e:
        error_exit("monitor", "read_error", str(e), args.json)
    finally:
        if text_buffer:
            if emit_line(text_buffer, cfg, args, include_re, exclude_re):
                line_count += 1
        ser.close()

    duration = round(time.time() - start_time, 1)
    summary = f"监控结束，共 {line_count} 行，耗时 {duration}s\n"
    sys.stderr.buffer.write(summary.encode("utf-8"))
    sys.stderr.buffer.flush()

    # 更新状态
    update_state_entry("last_observe", {
        "type": "serial_monitor",
        "port": cfg["port"],
        "baudrate": cfg["baudrate"],
        "lines": line_count,
        "duration_sec": duration,
    })


if __name__ == "__main__":
    main()
