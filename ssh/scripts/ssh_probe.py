#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys


REMOTE_PROBE = r'''set -u
value() { printf '%s=%s\n' "$1" "$2"; }
value os "$(uname -s 2>/dev/null || true)"
value arch "$(uname -m 2>/dev/null || true)"
value kernel "$(uname -r 2>/dev/null || true)"
value hostname "$(hostname 2>/dev/null || true)"
value cpu_count "$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
value memory_kib "$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || true)"
value python "$(python3 --version 2>&1 || true)"
value node "$(node --version 2>/dev/null || true)"
value gpu "$(nvidia-smi -L 2>/dev/null | paste -sd ';' - || true)"
value container "$(test -f /.dockerenv && printf docker || true)"
'''


def validate_alias(alias: str) -> None:
    if not alias or alias.startswith("-"):
        raise ValueError("alias must be a non-option OpenSSH Host name")
    if any(character.isspace() or ord(character) < 32 for character in alias):
        raise ValueError("alias must not contain whitespace or control characters")


def validate_arguments(args: argparse.Namespace) -> None:
    validate_alias(args.alias)
    if args.timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if args.connect_timeout <= 0:
        raise ValueError("connect-timeout must be greater than zero")
    if args.known_hosts_file and any(
        ord(character) < 32 for character in args.known_hosts_file
    ):
        raise ValueError("known-hosts-file must not contain control characters")


def build_command(args: argparse.Namespace) -> list[str]:
    validate_arguments(args)
    command = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={args.connect_timeout}",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ForwardAgent=no",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "ProxyCommand=none",
        "-o",
        "StrictHostKeyChecking=accept-new"
        if args.accept_new_host_key
        else "StrictHostKeyChecking=yes",
    ]
    if args.known_hosts_file:
        command.extend(["-o", f"UserKnownHostsFile={args.known_hosts_file}"])
    command.extend([args.alias, REMOTE_PROBE])
    return command


def parse_probe_output(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            result[key] = value
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect a fixed read-only environment summary through OpenSSH"
    )
    parser.add_argument("alias")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--connect-timeout", type=int, default=10)
    parser.add_argument("--accept-new-host-key", action="store_true")
    parser.add_argument("--known-hosts-file")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        command = build_command(args)
    except ValueError as exc:
        print(json.dumps({
            "success": False,
            "error": str(exc),
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps({
            "success": True,
            "dry_run": True,
            "alias": args.alias,
            "command": command[:-1] + ["<fixed-read-only-probe>"],
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired as exc:
        print(json.dumps({
            "success": False,
            "alias": args.alias,
            "exit_code": -1,
            "stderr": f"timeout after {exc.timeout}s",
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 124

    result = {
        "success": process.returncode == 0,
        "alias": args.alias,
        "exit_code": process.returncode,
        "environment": parse_probe_output(process.stdout),
        "stderr": process.stderr,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
