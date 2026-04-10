"""J-Link GDB Server 调试

启动 JLinkGDBServerCL 并通过 arm-none-eabi-gdb 提供源码级调试能力。
支持：加载 ELF、设置断点、单步、查看变量、调用栈等。

依赖：
- JLinkGDBServerCL.exe（SEGGER 安装包自带）
- arm-none-eabi-gdb（需单独安装 Arm GNU Toolchain）
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


def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def find_free_port() -> int:
    """找一个空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def start_gdbserver(gdbserver_exe: str, device: str, interface: str = "SWD",
                    speed: str = "4000", serial_no: str = "",
                    gdb_port: int = 0) -> tuple:
    """启动 JLinkGDBServerCL 后台进程，返回 (proc, port)"""
    if not gdb_port:
        gdb_port = find_free_port()

    cmd = [
        gdbserver_exe,
        "-device", device,
        "-if", interface,
        "-speed", speed,
        "-port", str(gdb_port),
        "-noir",
        "-LocalhostOnly",
        "-nologtofile",
        "-singlerun",  # 调试完自动退出
    ]
    if serial_no:
        cmd.extend(["-select", f"USB={serial_no}"])

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    return proc, gdb_port


def wait_gdbserver_ready(proc: subprocess.Popen, timeout: int = 15) -> bool:
    """等待 GDB Server 就绪"""
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            return False
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.1)
            continue
        if "Waiting for GDB connection" in line or "Connected to target" in line:
            return True
        if "Cannot connect" in line or "Could not connect" in line:
            return False
    return False


def run_gdb_commands(gdb_exe: str, elf_file: str, gdb_port: int,
                     commands: list) -> dict:
    """通过 arm-none-eabi-gdb 执行调试命令"""
    # 构建 GDB 批处理命令
    gdb_init = [
        f"target remote localhost:{gdb_port}",
    ]
    if elf_file:
        gdb_init.insert(0, f"file {elf_file}")

    all_commands = gdb_init + commands + ["quit"]

    # 构建 GDB 命令行
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
        # 解析调用栈
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
        # 解析局部变量
        var_lines = re.findall(r"^(\w+)\s*=\s*(.+)$", stdout, re.MULTILINE)
        if var_lines:
            result["variables"] = {name: val.strip() for name, val in var_lines}

    elif action == "print":
        # 解析 print 输出: $N = VALUE
        m = re.search(r"\$\d+\s*=\s*(.+)", stdout)
        if m:
            result["value"] = m.group(1).strip()

    return result


def cleanup(procs: list):
    """清理所有子进程"""
    for proc in procs:
        if proc and proc.poll() is None:
            try:
                if sys.platform == "win32":
                    proc.terminate()
                else:
                    proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                proc.kill()


def main():
    parser = argparse.ArgumentParser(description="J-Link GDB Server 调试")
    sub = parser.add_subparsers(dest="command")

    # gdb-start: 启动 GDB Server 并执行调试命令
    run_p = sub.add_parser("run", help="启动 GDB Server 并执行调试命令序列")
    run_p.add_argument("--gdbserver-exe", required=True, help="JLinkGDBServerCL.exe 路径")
    run_p.add_argument("--gdb-exe", required=True, help="arm-none-eabi-gdb 路径")
    run_p.add_argument("--device", required=True, help="芯片型号")
    run_p.add_argument("--elf", default="", help="ELF 文件路径（提供源码级调试）")
    run_p.add_argument("--interface", default="SWD")
    run_p.add_argument("--speed", default="4000")
    run_p.add_argument("--serial-no", default="")
    run_p.add_argument("--gdb-port", type=int, default=0, help="GDB 端口，0=自动")
    run_p.add_argument("--commands", nargs="+", required=True,
                       help="GDB 命令序列，如 'break main' 'continue' 'backtrace' 'info locals'")
    run_p.add_argument("--json", action="store_true", dest="as_json")

    # backtrace: 快捷命令 - 获取调用栈
    bt_p = sub.add_parser("backtrace", help="获取当前调用栈")
    bt_p.add_argument("--gdbserver-exe", required=True)
    bt_p.add_argument("--gdb-exe", required=True)
    bt_p.add_argument("--device", required=True)
    bt_p.add_argument("--elf", default="")
    bt_p.add_argument("--interface", default="SWD")
    bt_p.add_argument("--speed", default="4000")
    bt_p.add_argument("--serial-no", default="")
    bt_p.add_argument("--json", action="store_true", dest="as_json")

    # locals: 快捷命令 - 查看局部变量
    loc_p = sub.add_parser("locals", help="查看当前帧局部变量")
    loc_p.add_argument("--gdbserver-exe", required=True)
    loc_p.add_argument("--gdb-exe", required=True)
    loc_p.add_argument("--device", required=True)
    loc_p.add_argument("--elf", default="")
    loc_p.add_argument("--interface", default="SWD")
    loc_p.add_argument("--speed", default="4000")
    loc_p.add_argument("--serial-no", default="")
    loc_p.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 检查工具
    if not os.path.isfile(args.gdbserver_exe):
        result = {
            "status": "error", "action": args.command,
            "error": {"code": "gdbserver_not_found",
                      "message": f"JLinkGDBServerCL.exe 不存在: {args.gdbserver_exe}"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(args.gdb_exe):
        result = {
            "status": "error", "action": args.command,
            "error": {"code": "gdb_not_found",
                      "message": f"arm-none-eabi-gdb 不存在: {args.gdb_exe}。"
                                 "请安装 Arm GNU Toolchain: https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    procs = []
    try:
        # 启动 GDB Server
        gdb_proc, gdb_port = start_gdbserver(
            args.gdbserver_exe, args.device, args.interface,
            args.speed, args.serial_no,
            getattr(args, "gdb_port", 0),
        )
        procs.append(gdb_proc)

        if not wait_gdbserver_ready(gdb_proc):
            stderr_out = ""
            if gdb_proc.poll() is not None:
                stderr_out = gdb_proc.stderr.read()
            result = {
                "status": "error", "action": args.command,
                "error": {"code": "gdbserver_failed",
                          "message": f"GDB Server 启动失败。{stderr_out}".strip()},
            }
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {result['error']['message']}", file=sys.stderr)
            cleanup(procs)
            sys.exit(1)

        # 构建 GDB 命令
        if args.command == "run":
            gdb_commands = list(args.commands)
        elif args.command == "backtrace":
            gdb_commands = ["backtrace"]
        elif args.command == "locals":
            gdb_commands = ["info locals"]

        # 执行 GDB 命令
        gdb_result = run_gdb_commands(
            args.gdb_exe, args.elf, gdb_port, gdb_commands,
        )

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
                    "device": args.device,
                    "gdb_port": gdb_port,
                    "output": gdb_result["stdout"],
                    **parsed,
                },
            }

        if args.as_json:
            output_json(result)
        else:
            if result["status"] == "ok":
                print(f"[gdb-{args.command}] 成功")
                print(result["details"].get("output", ""))
            else:
                print(f"[gdb-{args.command}] 失败 — {result['error']['message']}", file=sys.stderr)
                sys.exit(1)

    finally:
        cleanup(procs)


if __name__ == "__main__":
    main()
