"""串口数据发送"""

import argparse
import json
import sys
import time
from pathlib import Path

from serial_runtime import (
    get_serial_config,
    get_mux_info,
    open_serial_port,
    save_project_config,
    update_state_entry,
)

PARITY_MAP = {"none": "N", "even": "E", "odd": "O", "mark": "M", "space": "S"}


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
        except ValueError:
            return None
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
    parser.add_argument("--port", help="串口号 (如 COM3)")
    parser.add_argument("--baudrate", type=int, help="波特率")
    parser.add_argument("--bytesize", type=int, help="数据位")
    parser.add_argument("--parity", help="校验位 (none/even/odd)")
    parser.add_argument("--stopbits", type=int, help="停止位")
    parser.add_argument("--encoding", help="编码")
    parser.add_argument("--hex", action="store_true", help="以 Hex 模式发送")
    parser.add_argument("--cr", action="store_true", help="追加 CR")
    parser.add_argument("--lf", action="store_true", help="追加 LF")
    parser.add_argument("--crlf", action="store_true", help="追加 CRLF")
    parser.add_argument("--repeat", type=int, default=1, help="重复次数")
    parser.add_argument("--interval", type=float, default=0.1, help="重复间隔（秒）")
    parser.add_argument("--wait-response", action="store_true", help="等待响应")
    parser.add_argument("--response-timeout", type=float, default=2.0, help="响应超时（秒）")
    parser.add_argument("--direct", action="store_true", help="直连真实串口，跳过 mux")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

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

    line_ending = "crlf" if args.crlf else ("cr" if args.cr else ("lf" if args.lf else ""))

    payload = build_payload(args.data, args.hex, line_ending)
    if payload is None:
        error_exit("bad_hex", "Hex 解析失败，请检查输入格式", args.json)

    try:
        use_mux = not args.direct
        ser = open_serial_port(cfg, use_mux=use_mux)
        if use_mux and get_mux_info():
            print("[mux] 警告: 通过多路复用发送数据，如 minicom 同时在写入会导致串口数据冲突", file=sys.stderr)
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
                        entry["rx"] = rx_raw.decode(cfg["encoding"], errors="replace")
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
        "summary": f"已发送 {args.repeat} 次到 {cfg['port']}@{cfg['baudrate']}",
        "details": details,
    }

    if args.json:
        output_json(result)
    else:
        for r in results:
            print(f"TX[{r['seq']}]: {r['tx']}")
            if "rx" in r:
                print(f"RX[{r['seq']}]: {r['rx']}")

    # 更新状态
    update_state_entry("last_serial_send", {
        "port": cfg["port"],
        "baudrate": cfg["baudrate"],
        "bytes_sent": len(payload) * args.repeat,
    })


if __name__ == "__main__":
    main()
