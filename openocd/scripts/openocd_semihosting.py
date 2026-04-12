"""OpenOCD Semihosting 输出捕获

工作流程：
1. 启动 OpenOCD 作为后台 Server
2. 连接 Telnet 端口
3. halt -> arm semihosting enable -> resume
4. 持续读取 OpenOCD stderr 中的 semihosting 输出
5. Ctrl+C 退出时发送 halt + arm semihosting disable + shutdown
"""

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime


# ── OpenOCD 服务器（复用 openocd_gdb.py 模式） ──────────────────

def build_openocd_cmd(exe: str, board: str = "", interface: str = "", target: str = "",
                      search: str = "", adapter_speed: str = "", transport: str = "",
                      gdb_port: int = 3333, telnet_port: int = 4444) -> list:
    """构建 OpenOCD 命令行"""
    cmd = [exe]
    if search:
        cmd.extend(["-s", search])
    if board:
        cmd.extend(["-f", board])
    else:
        if interface:
            cmd.extend(["-f", interface])
        if target:
            cmd.extend(["-f", target])
    if adapter_speed:
        cmd.extend(["-c", f"adapter speed {adapter_speed}"])
    if transport:
        cmd.extend(["-c", f"transport select {transport}"])
    cmd.extend(["-c", f"gdb_port {gdb_port}"])
    cmd.extend(["-c", f"telnet_port {telnet_port}"])
    return cmd


def start_openocd_server(cmd: list) -> subprocess.Popen:
    """启动 OpenOCD 进程"""
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )


def wait_server_ready(proc: subprocess.Popen, telnet_port: int, timeout: int = 15) -> tuple:
    """等待 OpenOCD 就绪"""
    start = time.time()
    errors = []
    ready = False
    while time.time() - start < timeout:
        if proc.poll() is not None:
            remaining = proc.stderr.read()
            for line in remaining.splitlines():
                if "Error:" in line:
                    errors.append(line.strip())
            return False, errors
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.1)
            continue
        line = line.strip()
        if "Error:" in line:
            errors.append(line)
        if f"Listening on port {telnet_port}" in line or "listening on" in line.lower():
            ready = True

    if not ready:
        return False, errors

    critical_keywords = [
        "open failed", "init mode failed", "no device found",
        "cannot connect", "error connecting dp", "examination failed",
    ]
    critical_errors = [e for e in errors if any(k in e.lower() for k in critical_keywords)]
    if critical_errors:
        return False, critical_errors
    return True, errors


def cleanup_proc(proc: subprocess.Popen):
    """清理 OpenOCD 进程"""
    if proc and proc.poll() is None:
        try:
            if sys.platform == "win32":
                proc.terminate()
            else:
                proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()


# ── Telnet 简易连接（仅用于发送启用命令） ─────────────────────

def telnet_send(host: str, port: int, command: str, timeout: float = 5.0) -> str:
    """连接 Telnet 端口，发送单条命令，返回响应"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        # 读取初始提示符
        _read_until_prompt(sock)
        # 发送命令
        sock.sendall((command + "\n").encode("utf-8"))
        response = _read_until_prompt(sock)
        return response
    finally:
        sock.close()


def telnet_send_multi(host: str, port: int, commands: list, timeout: float = 5.0) -> list:
    """连接 Telnet 端口，发送多条命令，返回响应列表"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    responses = []
    try:
        sock.connect((host, port))
        _read_until_prompt(sock)
        for cmd in commands:
            sock.sendall((cmd + "\n").encode("utf-8"))
            response = _read_until_prompt(sock)
            responses.append(response)
        return responses
    finally:
        sock.close()


def _read_until_prompt(sock: socket.socket) -> str:
    """读取直到 '> ' 提示符"""
    buf = b""
    while True:
        decoded = buf.decode("utf-8", errors="replace")
        if decoded.endswith("> ") or "\n> " in decoded or "\r> " in decoded:
            # 去掉提示符
            prompt_pos = decoded.rfind("\n> ")
            if prompt_pos == -1:
                prompt_pos = decoded.rfind("\r> ")
            if prompt_pos == -1 and decoded.endswith("> "):
                prompt_pos = len(decoded) - 2
            if prompt_pos >= 0:
                return decoded[:prompt_pos].strip()
            return decoded.strip()
        try:
            chunk = sock.recv(4096)
            if not chunk:
                return buf.decode("utf-8", errors="replace").strip()
            buf += chunk
        except socket.timeout:
            return buf.decode("utf-8", errors="replace").strip()


# ── Semihosting 输出过滤 ──────────────────────────────────────

# OpenOCD 自身日志行的前缀
LOG_PREFIXES = re.compile(r"^(Info|Warn|Error|Debug)\s*:", re.IGNORECASE)
# OpenOCD 状态行
STATUS_PATTERNS = [
    "Listening on port",
    "halted due to",
    "target state:",
    "shutdown command invoked",
    "GDB",
    "accepting",
    "dropped",
]


def is_semihosting_line(line: str) -> bool:
    """判断是否为 semihosting 输出（而非 OpenOCD 自身日志）"""
    stripped = line.strip()
    if not stripped:
        return False
    if LOG_PREFIXES.match(stripped):
        return False
    for pat in STATUS_PATTERNS:
        if pat in stripped:
            return False
    return True


def output_semihosting_line(text: str, as_json: bool = False):
    """输出一行 semihosting 数据"""
    if as_json:
        record = {
            "timestamp": datetime.now().isoformat(),
            "channel": 0,
            "text": text.rstrip(),
        }
        print(json.dumps(record, ensure_ascii=False), flush=True)
    else:
        print(text, end="", flush=True)


# ── 主逻辑 ────────────────────────────────────────────────────

def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description="OpenOCD Semihosting 输出捕获")
    parser.add_argument("--exe", default="openocd", help="openocd 路径")
    parser.add_argument("--board", default="", help="board 配置文件")
    parser.add_argument("--interface", default="", help="interface 配置文件")
    parser.add_argument("--target", default="", help="target 配置文件")
    parser.add_argument("--search", default="", help="额外配置脚本搜索目录")
    parser.add_argument("--adapter-speed", default="", help="调试速率 kHz")
    parser.add_argument("--transport", default="", choices=["", "swd", "jtag"], help="传输协议")
    parser.add_argument("--gdb-port", type=int, default=3333, help="GDB 端口")
    parser.add_argument("--telnet-port", type=int, default=4444, help="Telnet 端口")
    parser.add_argument("--timeout", type=int, default=0, help="捕获时长秒数，0=持续到 Ctrl+C")
    parser.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    # 参数校验
    if not args.board and not args.interface and not args.target:
        result = {
            "status": "error", "action": "semihosting",
            "error": {"code": "missing_config", "message": "必须提供 --board 或 --interface + --target"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr, flush=True)
        sys.exit(1)

    # 启动 OpenOCD
    cmd = build_openocd_cmd(
        exe=args.exe, board=args.board, interface=args.interface, target=args.target,
        search=args.search, adapter_speed=args.adapter_speed, transport=args.transport,
        gdb_port=args.gdb_port, telnet_port=args.telnet_port,
    )

    proc = None
    try:
        proc = start_openocd_server(cmd)
        ready, errors = wait_server_ready(proc, args.telnet_port)

        if not ready:
            error_msg = "; ".join(errors) if errors else "OpenOCD 启动失败或超时"
            result = {
                "status": "error", "action": "semihosting",
                "error": {"code": "server_failed", "message": error_msg},
            }
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {error_msg}", file=sys.stderr, flush=True)
            sys.exit(1)

        # 通过 Telnet 启用 semihosting
        try:
            responses = telnet_send_multi(
                "localhost", args.telnet_port,
                ["halt", "arm semihosting enable", "resume"],
            )
        except (ConnectionError, OSError) as e:
            result = {
                "status": "error", "action": "semihosting",
                "error": {"code": "telnet_failed", "message": f"Telnet 连接失败: {e}"},
            }
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {result['error']['message']}", file=sys.stderr, flush=True)
            sys.exit(1)

        if not args.as_json:
            print("Semihosting 已启用，等待输出（Ctrl+C 退出）:", file=sys.stderr, flush=True)
            print("-" * 40, file=sys.stderr, flush=True)

        # 持续读取 OpenOCD stderr 中的 semihosting 输出
        start_time = time.time()
        while True:
            if args.timeout > 0 and (time.time() - start_time) >= args.timeout:
                break

            if proc.poll() is not None:
                # OpenOCD 进程已退出，读取剩余输出
                remaining = proc.stderr.read()
                for line in remaining.splitlines(keepends=True):
                    if is_semihosting_line(line):
                        output_semihosting_line(line, args.as_json)
                break

            line = proc.stderr.readline()
            if not line:
                time.sleep(0.01)
                continue

            if is_semihosting_line(line):
                output_semihosting_line(line, args.as_json)

    except FileNotFoundError:
        result = {
            "status": "error", "action": "semihosting",
            "error": {"code": "exe_not_found", "message": f"openocd 不存在或不在 PATH 中: {args.exe}"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr, flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        if not args.as_json:
            print("\n已停止 semihosting 捕获", file=sys.stderr, flush=True)
    finally:
        # 尝试通过 Telnet 禁用 semihosting
        if proc and proc.poll() is None:
            try:
                telnet_send_multi(
                    "localhost", args.telnet_port,
                    ["halt", "arm semihosting disable", "shutdown"],
                    timeout=2.0,
                )
            except (ConnectionError, OSError):
                pass
        if proc:
            cleanup_proc(proc)


if __name__ == "__main__":
    main()
