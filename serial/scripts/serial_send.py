"""串口数据发送"""

import argparse
import json
import sys
import time
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
    result = {"status": "error", "action": "send", "error": {"code": code, "message": message}}
    if use_json:
        output_json(result)
    else:
        print(f"错误: {message}", file=sys.stderr)
    sys.exit(1)


def build_payload(data, hex_mode, line_ending):
    if hex_mode:
        try:
            clean = data.replace(" ", "").replace("0x", "").replace(",", "")
            return bytes.fromhex(clean)
        except ValueError as e:
            return None, f"Hex 解析失败: {e}"
    else:
        payload = data.encode("utf-8")
        if line_ending == "cr":
            payload += b"\r"
        elif line_ending == "lf":
            payload += b"\n"
        elif line_ending == "crlf":
            payload += b"\r\n"
        return payload


def main():
    parser = argparse.ArgumentParser(description="串口数据发送")
    parser.add_argument("data", help="要发送的数据")
    parser.add_argument("--hex", action="store_true", help="以 Hex 模式发送")
    parser.add_argument("--cr", action="store_true", help="追加 CR")
    parser.add_argument("--lf", action="store_true", help="追加 LF")
    parser.add_argument("--crlf", action="store_true", help="追加 CRLF")
    parser.add_argument("--repeat", type=int, default=1, help="重复次数")
    parser.add_argument("--interval", type=float, default=0.1, help="重复间隔（秒）")
    parser.add_argument("--wait-response", action="store_true", help="等待响应")
    parser.add_argument("--response-timeout", type=float, default=2.0, help="响应超时（秒）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.get("default_port"):
        error_exit("no_port", "config.json 中未配置 default_port", args.json)
    if not cfg.get("default_baudrate"):
        error_exit("no_baudrate", "config.json 中未配置 default_baudrate", args.json)

    line_ending = "crlf" if args.crlf else ("cr" if args.cr else ("lf" if args.lf else ""))

    payload = build_payload(args.data, args.hex, line_ending)
    if payload is None:
        error_exit("bad_hex", str(payload), args.json)

    try:
        ser = open_serial(cfg)
    except Exception as e:
        error_exit("connect_failed", str(e), args.json)

    results = []
    try:
        for i in range(args.repeat):
            ser.write(payload)
            ser.flush()
            tx_display = payload.hex(" ") if args.hex else args.data

            entry = {"seq": i + 1, "tx": tx_display, "tx_bytes": len(payload)}

            if args.wait_response:
                ser.timeout = args.response_timeout
                rx_raw = ser.read(4096)
                if rx_raw:
                    try:
                        entry["rx"] = rx_raw.decode(cfg.get("default_encoding", "utf-8"), errors="replace")
                    except Exception:
                        entry["rx"] = rx_raw.hex(" ")
                    entry["rx_bytes"] = len(rx_raw)
                else:
                    entry["rx"] = ""
                    entry["rx_bytes"] = 0

            results.append(entry)

            if args.repeat > 1 and i < args.repeat - 1:
                time.sleep(args.interval)
    except Exception as e:
        error_exit("write_error", str(e), args.json)
    finally:
        ser.close()

    if args.repeat == 1:
        details = results[0]
    else:
        details = {"rounds": results, "total": len(results)}

    result = {
        "status": "ok",
        "action": "send",
        "summary": f"已发送 {args.repeat} 次到 {cfg['default_port']}@{cfg.get('default_baudrate')}",
        "details": details,
    }

    if args.json:
        output_json(result)
    else:
        for r in results:
            print(f"TX[{r['seq']}]: {r['tx']}")
            if "rx" in r:
                print(f"RX[{r['seq']}]: {r['rx']}")


if __name__ == "__main__":
    main()
