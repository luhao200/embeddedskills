"""Serial 多路复用管理 — 基于 socat 将真实串口桥接到 TCP + 虚拟 PTY"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from serial_runtime import (
    get_serial_config,
    load_workspace_state,
    save_workspace_state,
    is_missing,
    make_result,
    output_json,
)

DEFAULT_MUX_PORT = 20001
DEFAULT_VSERIAL_LINK = "/tmp/serial_mux_vserial"
STATE_KEY = "serial_mux"


def find_free_port(start: int = DEFAULT_MUX_PORT) -> int:
    """从 start 开始找空闲 TCP 端口"""
    for offset in range(100):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def build_socat_serial_opts(config: dict) -> str:
    """将 serial 配置转为 socat 串口选项字符串"""
    opts = ["raw", "echo=0"]
    opts.append(f"b{config['baudrate']}")
    opts.append(f"cs{config['bytesize']}")

    parity = config.get("parity", "none")
    if parity == "none":
        opts.append("-parenb")
    elif parity == "odd":
        opts.append("parenb,parodd")
    elif parity == "even":
        opts.append("parenb,-parodd")
    else:
        opts.append("parenb")

    stopbits = config.get("stopbits", 1)
    if stopbits == 2:
        opts.append("cstopb")
    else:
        opts.append("-cstopb")

    return ",".join(opts)


def start_mux(port: str, baudrate: int | None, workspace: str | None, vserial_link: str):
    """启动 socat 多路复用"""
    if not shutil.which("socat"):
        return make_result(
            success=False,
            action="mux_start",
            summary="socat 未安装",
            error={"code": "socat_missing", "message": "请安装 socat: apt install socat / pacman -S socat"},
        )

    # 检查已运行的 mux
    state = load_workspace_state(workspace)
    existing = state.get(STATE_KEY)
    if existing:
        if is_mux_alive(existing):
            return make_result(
                success=False,
                action="mux_start",
                summary="Mux 已在运行",
                error={"code": "already_running", "message": f"Mux 已在运行 (TCP:{existing['tcp_port']}, PTY:{existing['vserial']})"},
                details=existing,
            )
        else:
            # 清理僵尸状态
            state.pop(STATE_KEY, None)
            save_workspace_state(state, workspace)

    # 获取串口配置
    cfg, sources = get_serial_config(
        cli_port=port,
        cli_baudrate=baudrate,
        workspace=workspace,
    )

    if cfg is None:
        return make_result(
            success=False,
            action="mux_start",
            summary="无法获取串口配置",
            error={"code": "config_error", "message": sources.get("error", "配置错误")},
        )

    if is_missing(cfg["port"]):
        return make_result(
            success=False,
            action="mux_start",
            summary="未指定串口",
            error={"code": "no_port", "message": "请用 --port 指定串口"},
        )

    tcp_port = find_free_port()

    serial_opts = build_socat_serial_opts(cfg)
    real_port = cfg["port"]

    # Layer 1: 真实串口 → TCP server (fork 模式)
    cmd1 = ["socat", "-d", "-d", f"{real_port},{serial_opts}", f"TCP-LISTEN:{tcp_port},reuseaddr,fork"]
    # Layer 2: TCP client → 虚拟 PTY (供 minicom)
    cmd2 = ["socat", "-d", "-d", f"PTY,link={vserial_link},raw,echo=0", f"TCP:127.0.0.1:{tcp_port}"]

    try:
        p1 = subprocess.Popen(cmd1, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.3)
        if p1.poll() is not None:
            return make_result(
                success=False,
                action="mux_start",
                summary=f"无法打开串口 {real_port}",
                error={"code": "port_open_failed", "message": f"串口 {real_port} 打开失败，请检查是否被占用"},
            )

        p2 = subprocess.Popen(cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.3)
        if p2.poll() is not None:
            p1.terminate()
            p1.wait()
            return make_result(
                success=False,
                action="mux_start",
                summary="无法创建虚拟串口",
                error={"code": "pty_failed", "message": "虚拟 PTY 创建失败"},
            )

        if not os.path.exists(vserial_link):
            p1.terminate()
            p2.terminate()
            p1.wait()
            p2.wait()
            return make_result(
                success=False,
                action="mux_start",
                summary="虚拟串口未创建",
                error={"code": "pty_not_created", "message": f"PTY 链接 {vserial_link} 未创建"},
            )

    except Exception as e:
        return make_result(
            success=False,
            action="mux_start",
            summary="启动失败",
            error={"code": "start_failed", "message": str(e)},
        )

    mux_info = {
        "tcp_port": tcp_port,
        "tcp_pid": p1.pid,
        "pty_pid": p2.pid,
        "vserial": vserial_link,
        "real_port": real_port,
        "baudrate": cfg["baudrate"],
        "bytesize": cfg.get("bytesize", 8),
        "parity": cfg.get("parity", "none"),
        "stopbits": cfg.get("stopbits", 1),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    # 保存状态
    state[STATE_KEY] = mux_info
    save_workspace_state(state, workspace)

    return make_result(
        success=True,
        action="mux_start",
        summary=f"Mux 已启动: {real_port} -> TCP:{tcp_port} -> PTY:{vserial_link}",
        details=mux_info,
    )


def stop_mux(workspace: str | None = None):
    """停止 socat 多路复用"""
    state = load_workspace_state(workspace)
    mux_info = state.get(STATE_KEY)

    if not mux_info:
        return make_result(
            success=False,
            action="mux_stop",
            summary="未找到运行中的 Mux",
            error={"code": "not_running", "message": "未找到运行中的串口多路复用"},
        )

    killed = []
    failed = []
    for pid_key in ("tcp_pid", "pty_pid"):
        pid = mux_info.get(pid_key)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
            except ProcessLookupError:
                pass
            except Exception:
                failed.append(str(pid))

    # 清理残留的虚拟串口符号链接
    vserial = mux_info.get("vserial")
    if vserial and os.path.islink(vserial):
        try:
            os.unlink(vserial)
        except OSError:
            pass

    # 清理状态
    state.pop(STATE_KEY, None)
    save_workspace_state(state, workspace)

    if failed:
        return make_result(
            success=True,
            action="mux_stop",
            summary=f"已终止 {len(killed)} 个进程，{len(failed)} 个失败",
            details={"killed": killed, "failed": failed, "vserial": mux_info.get("vserial")},
        )
    else:
        return make_result(
            success=True,
            action="mux_stop",
            summary=f"Mux 已停止 ({len(killed)} 个进程已终止)",
            details={"killed": killed, "vserial": mux_info.get("vserial")},
        )


def is_mux_alive(mux_info: dict) -> bool:
    """检查 mux 进程是否存活"""
    for pid_key in ("tcp_pid", "pty_pid"):
        pid = mux_info.get(pid_key)
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
    return True


def status_mux(workspace: str | None = None):
    """查询 mux 状态"""
    state = load_workspace_state(workspace)
    mux_info = state.get(STATE_KEY)

    if not mux_info:
        return make_result(
            success=True,
            action="mux_status",
            summary="Mux 未运行",
            details={"running": False},
        )

    alive = is_mux_alive(mux_info)
    if not alive:
        state.pop(STATE_KEY, None)
        save_workspace_state(state, workspace)
        return make_result(
            success=True,
            action="mux_status",
            summary="Mux 已停止（清理残留状态）",
            details={"running": False, "cleaned": True},
        )

    return make_result(
        success=True,
        action="mux_status",
        summary=f"Mux 运行中: {mux_info.get('real_port')} -> TCP:{mux_info.get('tcp_port')} -> PTY:{mux_info.get('vserial')}",
        details={
            "running": True,
            "real_port": mux_info.get("real_port"),
            "tcp_port": mux_info.get("tcp_port"),
            "vserial": mux_info.get("vserial"),
            "baudrate": mux_info.get("baudrate"),
            "started_at": mux_info.get("started_at"),
        },
    )


def main():
    parser = argparse.ArgumentParser(description="Serial 多路复用管理")
    sub = parser.add_subparsers(dest="command", help="子命令")

    p_start = sub.add_parser("start", help="启动多路复用")
    p_start.add_argument("--port", help="真实串口号 (如 /dev/ttyUSB0)")
    p_start.add_argument("--baudrate", type=int, help="波特率")
    p_start.add_argument("--vserial", default=DEFAULT_VSERIAL_LINK, help=f"虚拟串口路径 (默认: {DEFAULT_VSERIAL_LINK})")
    p_start.add_argument("--workspace", help="工作区路径")

    sub.add_parser("stop", help="停止多路复用").add_argument("--workspace", help="工作区路径")

    sub.add_parser("status", help="查询多路复用状态").add_argument("--workspace", help="工作区路径")

    args = parser.parse_args()

    if args.command == "start":
        result = start_mux(args.port, args.baudrate, getattr(args, "workspace", None), args.vserial)
    elif args.command == "stop":
        result = stop_mux(getattr(args, "workspace", None))
    elif args.command == "status":
        result = status_mux(getattr(args, "workspace", None))
    else:
        result = status_mux()
        if result["details"].get("running"):
            print(f"Mux 运行中: {result['details']['real_port']} -> TCP:{result['details']['tcp_port']} -> PTY:{result['details']['vserial']}")
            print(f"  虚拟串口: {result['details']['vserial']}")
            print(f"  TCP 端口: {result['details']['tcp_port']}")
            print(f"  启动时间: {result['details']['started_at']}")
        else:
            print("Mux 未运行. 使用 'start --port <串口>' 启动")

    output_json(result)


if __name__ == "__main__":
    main()
