"""OpenOCD GDB Server 启动与调试

子命令：
- server: 启动 GDB Server 并保持运行（原有行为）
- run:    启动 GDB Server + 执行自定义 GDB 命令序列 + 关闭
- backtrace: 快捷获取调用栈
- locals: 快捷查看局部变量
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time


def build_openocd_cmd(exe: str, board: str = "", interface: str = "", target: str = "",
                      search: str = "", adapter_speed: str = "", transport: str = "",
                      gdb_port: int = 3333, telnet_port: int = 4444) -> list:
    """构建 OpenOCD GDB Server 命令行"""
    cmd = [exe]

    if search:
        cmd.extend(["-s", search])

    # board 优先
    if board:
        cmd.extend(["-f", board])
    else:
        if interface:
            cmd.extend(["-f", interface])
        if target:
            cmd.extend(["-f", target])

    # adapter speed
    if adapter_speed:
        cmd.extend(["-c", f"adapter speed {adapter_speed}"])

    # transport
    if transport:
        cmd.extend(["-c", f"transport select {transport}"])

    # 端口配置
    cmd.extend(["-c", f"gdb_port {gdb_port}"])
    cmd.extend(["-c", f"telnet_port {telnet_port}"])

    return cmd


def start_openocd_server(cmd: list) -> subprocess.Popen:
    """启动 OpenOCD 进程"""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    return proc


def wait_server_ready(proc: subprocess.Popen, gdb_port: int, timeout: int = 15) -> tuple:
    """等待 GDB Server 就绪，返回 (ready: bool, errors: list)"""
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

        if f"Listening on port {gdb_port}" in line or "listening on" in line.lower():
            ready = True

    if not ready:
        return False, errors

    critical_keywords = [
        "open failed", "init mode failed", "no device found",
        "cannot connect", "error connecting dp", "examination failed",
        "failed to read memory", "failed to write memory",
        "cannot read idr", "polling failed",
    ]
    critical_errors = [e for e in errors if any(k in e.lower() for k in critical_keywords)]
    if critical_errors:
        return False, critical_errors

    return True, errors


def cleanup(proc: subprocess.Popen):
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


def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


# ── GDB 命令执行 ─────────────────────────────────────────────

def run_gdb_commands(gdb_exe: str, elf_file: str, gdb_port: int, commands: list) -> dict:
    """通过 arm-none-eabi-gdb 执行调试命令"""
    gdb_init = [f"target remote localhost:{gdb_port}"]
    if elf_file:
        gdb_init.insert(0, f"file {elf_file}")

    all_commands = gdb_init + commands + ["quit"]

    cmd = [gdb_exe, "--batch", "--nx"]
    for c in all_commands:
        cmd.extend(["-ex", c])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "GDB 执行超时(30s)"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def parse_gdb_output(stdout: str, action: str) -> dict:
    """解析 GDB 输出"""
    result = {}

    if action == "backtrace":
        frames = re.findall(
            r"#(\d+)\s+(?:0x[0-9a-fA-F]+\s+in\s+)?(\w+)\s*\(([^)]*)\)(?:\s+at\s+(.+))?",
            stdout
        )
        if frames:
            result["frames"] = []
            for num, func, args, location in frames:
                frame = {"frame": int(num), "function": func}
                if args:
                    frame["args"] = args.strip()
                if location:
                    frame["location"] = location.strip()
                result["frames"].append(frame)

    elif action == "locals":
        var_lines = re.findall(r"^(\w+)\s*=\s*(.+)$", stdout, re.MULTILINE)
        if var_lines:
            result["variables"] = {name: val.strip() for name, val in var_lines}

    return result


# ── 主逻辑 ────────────────────────────────────────────────────

def add_common_args(parser):
    """添加公共参数"""
    parser.add_argument("--exe", default="openocd", help="openocd 路径")
    parser.add_argument("--board", default="", help="board 配置文件")
    parser.add_argument("--interface", default="", help="interface 配置文件")
    parser.add_argument("--target", default="", help="target 配置文件")
    parser.add_argument("--search", default="", help="额外配置脚本搜索目录")
    parser.add_argument("--adapter-speed", default="", help="调试速率 kHz")
    parser.add_argument("--transport", default="", choices=["", "swd", "jtag"], help="传输协议")
    parser.add_argument("--gdb-port", type=int, default=3333, help="GDB 端口")
    parser.add_argument("--telnet-port", type=int, default=4444, help="Telnet 端口")
    parser.add_argument("--json", action="store_true", dest="as_json")


def validate_config(args) -> dict:
    """校验配置，返回 None 或 error dict"""
    if not args.board and not args.interface and not args.target:
        return {
            "status": "error",
            "action": getattr(args, "command", "server") or "server",
            "error": {"code": "missing_config", "message": "必须提供 --board 或 --interface + --target"},
        }
    return None


SUBCOMMANDS = ("server", "run", "backtrace", "locals")


def main():
    # 向后兼容：检测第一个参数是否为已知子命令
    # 如果不是（如 --interface ...），按 server 模式处理
    use_legacy = True
    if len(sys.argv) > 1 and sys.argv[1] in SUBCOMMANDS:
        use_legacy = False

    if use_legacy:
        # 旧版调用方式：直接传 --interface --target 等，无子命令
        server_parser = argparse.ArgumentParser(description="OpenOCD GDB Server 启动")
        add_common_args(server_parser)
        args = server_parser.parse_args()
        args.command = "server"
    else:
        parser = argparse.ArgumentParser(description="OpenOCD GDB Server 启动与调试")
        sub = parser.add_subparsers(dest="command")

        # server 子命令
        server_p = sub.add_parser("server", help="启动 GDB Server 并保持运行")
        add_common_args(server_p)

        # run 子命令
        run_p = sub.add_parser("run", help="启动 GDB Server 并执行 GDB 命令序列")
        add_common_args(run_p)
        run_p.add_argument("--gdb-exe", required=True, help="arm-none-eabi-gdb 路径")
        run_p.add_argument("--elf", default="", help="ELF 文件路径（提供源码级调试）")
        run_p.add_argument("--commands", nargs="+", required=True,
                           help="GDB 命令序列，如 'break main' 'continue' 'backtrace'")

        # backtrace 子命令
        bt_p = sub.add_parser("backtrace", help="获取当前调用栈")
        add_common_args(bt_p)
        bt_p.add_argument("--gdb-exe", required=True, help="arm-none-eabi-gdb 路径")
        bt_p.add_argument("--elf", default="", help="ELF 文件路径")

        # locals 子命令
        loc_p = sub.add_parser("locals", help="查看当前帧局部变量")
        add_common_args(loc_p)
        loc_p.add_argument("--gdb-exe", required=True, help="arm-none-eabi-gdb 路径")
        loc_p.add_argument("--elf", default="", help="ELF 文件路径")

        args = parser.parse_args()

    # 校验配置
    err = validate_config(args)
    if err:
        if args.as_json:
            output_json(err)
        else:
            print(f"错误: {err['error']['message']}", file=sys.stderr, flush=True)
        sys.exit(1)

    # 校验 GDB 工具路径
    if args.command in ("run", "backtrace", "locals"):
        if not os.path.isfile(args.gdb_exe):
            result = {
                "status": "error", "action": args.command,
                "error": {"code": "gdb_not_found",
                          "message": f"arm-none-eabi-gdb 不存在: {args.gdb_exe}。"
                                     "请安装 Arm GNU Toolchain"},
            }
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {result['error']['message']}", file=sys.stderr, flush=True)
            sys.exit(1)

    cmd = build_openocd_cmd(
        exe=args.exe, board=args.board, interface=args.interface, target=args.target,
        search=args.search, adapter_speed=args.adapter_speed, transport=args.transport,
        gdb_port=args.gdb_port, telnet_port=args.telnet_port,
    )

    proc = None
    try:
        proc = start_openocd_server(cmd)
        ready, errors = wait_server_ready(proc, args.gdb_port)

        if not ready:
            error_msg = "; ".join(errors) if errors else "GDB Server 启动失败或超时"
            result = {
                "status": "error",
                "action": args.command,
                "error": {"code": "gdbserver_failed", "message": error_msg},
            }
            cleanup(proc)
            if args.as_json:
                output_json(result)
            else:
                print(f"[{args.command}] 失败 — {error_msg}", file=sys.stderr, flush=True)
            sys.exit(1)

        # ── server 模式：保持运行 ──
        if args.command == "server":
            result = {
                "status": "ok",
                "action": "gdb-server",
                "summary": "GDB Server 已就绪",
                "details": {
                    "gdb_port": args.gdb_port,
                    "telnet_port": args.telnet_port,
                    "pid": proc.pid,
                    "connect_cmd": f"arm-none-eabi-gdb -ex 'target remote localhost:{args.gdb_port}'",
                },
            }
            if errors:
                result["details"]["warnings"] = errors

            if args.as_json:
                output_json(result)
            else:
                print(f"[gdb-server] GDB Server 已就绪", flush=True)
                print(f"  GDB 端口: {args.gdb_port}", flush=True)
                print(f"  Telnet 端口: {args.telnet_port}", flush=True)
                print(f"  PID: {proc.pid}", flush=True)
                print(f"  连接: arm-none-eabi-gdb -ex 'target remote localhost:{args.gdb_port}'", flush=True)
                if errors:
                    print(f"  警告: {'; '.join(errors)}", flush=True)

            # 保持运行
            try:
                proc.wait()
            except KeyboardInterrupt:
                pass
            return

        # ── run / backtrace / locals 模式：执行 GDB 命令后退出 ──
        if args.command == "run":
            gdb_commands = list(args.commands)
        elif args.command == "backtrace":
            gdb_commands = ["backtrace"]
        elif args.command == "locals":
            gdb_commands = ["info locals"]
        else:
            gdb_commands = []

        elf = getattr(args, "elf", "")
        gdb_result = run_gdb_commands(args.gdb_exe, elf, args.gdb_port, gdb_commands)

        if gdb_result["status"] == "error":
            result = {
                "status": "error", "action": args.command,
                "error": {"code": "gdb_error",
                          "message": gdb_result.get("error", gdb_result.get("stderr", "GDB 执行失败"))},
            }
        else:
            parsed = parse_gdb_output(gdb_result["stdout"], args.command)
            result = {
                "status": "ok",
                "action": args.command,
                "summary": f"GDB {args.command} 执行成功",
                "details": {
                    "gdb_port": args.gdb_port,
                    "output": gdb_result["stdout"],
                    **parsed,
                },
            }

        if args.as_json:
            output_json(result)
        else:
            if result["status"] == "ok":
                print(f"[gdb-{args.command}] 成功", flush=True)
                print(result["details"].get("output", ""), flush=True)
            else:
                err = result.get("error", {})
                print(f"[gdb-{args.command}] 失败 — {err.get('message', '未知错误')}", file=sys.stderr, flush=True)
                sys.exit(1)

    except FileNotFoundError:
        result = {
            "status": "error",
            "action": args.command,
            "error": {"code": "exe_not_found", "message": f"openocd 不存在或不在 PATH 中: {args.exe}"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr, flush=True)
        sys.exit(1)
    finally:
        if proc:
            cleanup(proc)


if __name__ == "__main__":
    main()
