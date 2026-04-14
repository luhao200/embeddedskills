"""CAN 总线监控：持续读取报文，支持过滤、DBC 解码和 CAN-FD"""

import argparse
import json
import sys
import time
from pathlib import Path

from can_runtime import (
    add_can_connection_args,
    get_can_config,
    open_can_bus,
    save_project_config,
    update_state_entry,
)


def parse_id_list(s):
    """解析逗号分隔的 CAN ID 列表，支持 0x 前缀"""
    if not s:
        return None
    ids = set()
    for part in s.split(","):
        part = part.strip()
        if part:
            ids.add(int(part, 0))
    return ids


def output_json_line(obj):
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def format_data(data):
    return " ".join(f"{b:02X}" for b in data)


def main():
    parser = argparse.ArgumentParser(description="CAN 总线实时监控")
    add_can_connection_args(parser, include_data_bitrate=True)
    parser.add_argument("--fd", action="store_true", help="启用 CAN-FD 模式")
    parser.add_argument("--filter-id", help="只显示指定 ID（逗号分隔）")
    parser.add_argument("--exclude-id", help="排除指定 ID（逗号分隔）")
    parser.add_argument("--dbc", help="DBC 数据库文件路径，用于解码")
    parser.add_argument("--timeout", type=float, help="监控时长（秒）")
    parser.add_argument("--json", action="store_true", help="JSON Lines 输出")
    args = parser.parse_args()

    try:
        import can
    except ImportError:
        err = {"status": "error", "action": "monitor", "error": {"code": "import_error", "message": "python-can 未安装，请执行 pip install python-can"}}
        if args.json:
            output_json_line(err)
        else:
            print(f"错误: {err['error']['message']}", file=sys.stderr)
        sys.exit(1)

    # 获取配置
    config, sources = get_can_config(
        cli_interface=args.interface,
        cli_channel=args.channel,
        cli_bitrate=args.bitrate,
        cli_data_bitrate=args.data_bitrate,
    )

    if config is None:
        if sources.get("need_selection"):
            err = {"status": "error", "action": "monitor", "error": {"code": "multiple_candidates", "message": f"{sources['error']}，请用 --interface 和 --channel 指定"}}
        else:
            err = {"status": "error", "action": "monitor", "error": {"code": "config_error", "message": sources.get("error", "配置错误")}}
        if args.json:
            output_json_line(err)
        else:
            print(f"错误: {err['error']['message']}", file=sys.stderr)
        sys.exit(1)

    interface = config["interface"]
    channel = config["channel"]
    bitrate = config["bitrate"]
    data_bitrate = config["data_bitrate"]

    # 保存确认的配置
    save_project_config(values={
        "interface": interface,
        "channel": channel,
        "bitrate": bitrate,
        "data_bitrate": data_bitrate,
    })

    filter_ids = parse_id_list(args.filter_id)
    exclude_ids = parse_id_list(args.exclude_id)

    # 加载 DBC
    db = None
    if args.dbc:
        try:
            import cantools
            db = cantools.database.load_file(args.dbc)
        except ImportError:
            print("警告: cantools 未安装，DBC 解码不可用", file=sys.stderr)
        except Exception as e:
            print(f"警告: 加载 DBC 失败: {e}", file=sys.stderr)

    # 连接总线
    try:
        bus = open_can_bus(config)
        if args.fd and data_bitrate:
            # 重新打开以启用 FD 模式
            bus = can.Bus(
                interface=interface,
                channel=channel,
                bitrate=bitrate,
                fd=True,
                data_bitrate=data_bitrate,
            )
    except Exception as e:
        err = {"status": "error", "action": "monitor", "error": {"code": "interface_open_failed", "message": str(e)}}
        if args.json:
            output_json_line(err)
        else:
            print(f"错误: 无法打开 CAN 接口 — {e}", file=sys.stderr)
        sys.exit(1)

    start = time.time()
    count = 0

    try:
        while True:
            if args.timeout and (time.time() - start) >= args.timeout:
                break

            remaining = None
            if args.timeout:
                remaining = args.timeout - (time.time() - start)
                if remaining <= 0:
                    break

            msg = bus.recv(timeout=min(remaining, 1.0) if remaining else 1.0)
            if msg is None:
                continue

            arb_id = msg.arbitration_id
            if filter_ids and arb_id not in filter_ids:
                continue
            if exclude_ids and arb_id in exclude_ids:
                continue

            count += 1

            # 尝试 DBC 解码
            decoded = None
            if db:
                try:
                    db_msg = db.get_message_by_frame_id(arb_id)
                    decoded = db_msg.decode(msg.data)
                    decoded = {k: round(v, 6) if isinstance(v, float) else v for k, v in decoded.items()}
                except Exception:
                    pass

            if args.json:
                obj = {
                    "timestamp": round(msg.timestamp, 6),
                    "id": f"0x{arb_id:03X}",
                    "dlc": msg.dlc,
                    "data": format_data(msg.data),
                    "is_fd": msg.is_fd,
                }
                if decoded:
                    obj["decoded"] = decoded
                output_json_line(obj)
            else:
                fd_flag = " [FD]" if msg.is_fd else ""
                dec_str = ""
                if decoded:
                    dec_str = " | " + ", ".join(f"{k}={v}" for k, v in decoded.items())
                print(f"[{msg.timestamp:.6f}] 0x{arb_id:03X} [{msg.dlc}] {format_data(msg.data)}{fd_flag}{dec_str}")

    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()
        elapsed = time.time() - start
        print(f"\n监控结束: {count} 帧, {elapsed:.1f} 秒", file=sys.stderr)

    # 更新状态
    update_state_entry("last_observe", {
        "type": "can_monitor",
        "interface": interface,
        "channel": channel,
        "frames": count,
        "duration_sec": round(elapsed, 1),
    })


if __name__ == "__main__":
    main()
