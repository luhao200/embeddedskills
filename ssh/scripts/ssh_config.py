#!/usr/bin/env python3
import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


LIST_METADATA_KEYS = {"aliases", "groups", "tags"}
TEXT_METADATA_KEYS = {"description", "location"}
SUPPORTED_METADATA_KEYS = LIST_METADATA_KEYS | TEXT_METADATA_KEYS


def ssh_config_path() -> Path:
    return Path.home() / ".ssh" / "config"


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def parse_hosts(lines: list[str]) -> list[dict]:
    hosts: list[dict] = []
    comments: list[str] = []
    current: dict | None = None

    def finish() -> None:
        nonlocal current
        if current:
            hosts.append(current)
            current = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") and current is None:
            comments.append(line)
            continue
        if not stripped and current is None:
            comments.append(line)
            continue

        if stripped.lower().startswith("host ") and not stripped.lower().startswith("host *"):
            finish()
            host_patterns = stripped.split(None, 1)[1].split()
            if not host_patterns:
                continue
            current = {
                "alias": host_patterns[0],
                "host_patterns": host_patterns,
                "options": {},
                "metadata": parse_metadata(comments),
                "raw_comments": comments,
            }
            comments = []
            continue

        if current and (line.startswith(" ") or line.startswith("\t")) and stripped:
            parts = stripped.split(None, 1)
            if len(parts) == 2:
                current["options"][parts[0].lower()] = parts[1]
            continue

        if current and not stripped:
            finish()
            comments = [line]
        else:
            comments = []

    finish()
    return hosts


def parse_metadata(comments: list[str]) -> dict:
    metadata: dict = {}
    for line in comments:
        text = line.strip()
        if not text.startswith("#"):
            continue
        text = text[1:].strip()
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in SUPPORTED_METADATA_KEYS:
            metadata[key] = value
    return metadata


def metadata_values(metadata: dict, key: str) -> list[str]:
    value = metadata.get(key, "")
    if key not in LIST_METADATA_KEYS:
        return [value] if value else []
    return [item.strip() for item in value.split(",") if item.strip()]


def searchable_values(host: dict) -> list[str]:
    metadata = host.get("metadata", {})
    values = list(host.get("host_patterns", []))
    values.extend(str(value) for value in host.get("options", {}).values())
    for key in SUPPORTED_METADATA_KEYS:
        values.extend(metadata_values(metadata, key))
    return values


def search_hosts(hosts: list[dict], terms: list[str]) -> list[dict]:
    normalized_terms = [term.strip().casefold() for term in terms if term.strip()]
    if not normalized_terms:
        return []

    matches: list[tuple[int, int, str, dict]] = []
    for host in hosts:
        values = searchable_values(host)
        folded_values = [value.casefold() for value in values]
        haystack = "\n".join(folded_values)
        if not all(term in haystack for term in normalized_terms):
            continue

        host_patterns = {
            item.casefold() for item in host.get("host_patterns", [])
        }
        aliases = {
            item.casefold()
            for item in metadata_values(host.get("metadata", {}), "aliases")
        }
        exact_pattern_matches = sum(
            term in host_patterns for term in normalized_terms
        )
        exact_alias_matches = sum(term in aliases for term in normalized_terms)
        matches.append((
            -exact_pattern_matches,
            -exact_alias_matches,
            host["alias"].casefold(),
            host,
        ))

    return [item[3] for item in sorted(matches, key=lambda item: item[:3])]


def resolve_hosts(hosts: list[dict], terms: list[str]) -> list[dict]:
    normalized_terms = [term.strip().casefold() for term in terms if term.strip()]
    if len(normalized_terms) == 1:
        exact_pattern_matches = [
            host
            for host in hosts
            if normalized_terms[0]
            in {item.casefold() for item in host.get("host_patterns", [])}
        ]
        if exact_pattern_matches:
            return exact_pattern_matches
    return search_hosts(hosts, terms)


def validate_single_line_field(name: str, value: object) -> None:
    if isinstance(value, str) and ("\n" in value or "\r" in value):
        raise ValueError(f"{name} must be a single line")


def backup_config(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    return backup


def run_ssh_g(alias: str) -> dict:
    proc = subprocess.run(
        ["ssh", "-G", alias],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return {"success": False, "stderr": proc.stderr.strip(), "config": {}}

    config: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if " " not in line:
            continue
        key, value = line.split(" ", 1)
        if key in {"hostname", "user", "port", "identityfile", "proxyjump"}:
            config[key] = value
    return {"success": True, "stderr": "", "config": config}


def cmd_list(_args: argparse.Namespace) -> int:
    hosts = parse_hosts(read_lines(ssh_config_path()))
    print(json.dumps({"success": True, "hosts": hosts}, ensure_ascii=False, indent=2))
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    matches = search_hosts(
        parse_hosts(read_lines(ssh_config_path())),
        args.query,
    )
    print(json.dumps({
        "success": True,
        "query": args.query,
        "count": len(matches),
        "hosts": matches,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    matches = resolve_hosts(
        parse_hosts(read_lines(ssh_config_path())),
        args.query,
    )
    if len(matches) == 1:
        host = matches[0]
        print(json.dumps({
            "success": True,
            "query": args.query,
            "host": host,
        }, ensure_ascii=False, indent=2))
        return 0

    error = "not_found" if not matches else "ambiguous"
    print(json.dumps({
        "success": False,
        "error": error,
        "query": args.query,
        "candidates": matches,
    }, ensure_ascii=False, indent=2), file=sys.stderr)
    return 1 if error == "not_found" else 2


def cmd_show(args: argparse.Namespace) -> int:
    resolved = run_ssh_g(args.alias)
    hosts = parse_hosts(read_lines(ssh_config_path()))
    local = next(
        (
            host
            for host in hosts
            if args.alias in host.get("host_patterns", [])
        ),
        None,
    )
    result = {
        "success": bool(resolved["success"] and local),
        "alias": args.alias,
        "defined": local is not None,
        "metadata": local.get("metadata", {}) if local else {},
        "options": local.get("options", {}) if local else {},
        "resolved": resolved["config"],
        "stderr": resolved["stderr"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


def cmd_add(args: argparse.Namespace) -> int:
    try:
        for name in (
            "alias",
            "host",
            "user",
            "key",
            "proxy_jump",
            "description",
            "aliases",
            "groups",
            "tags",
            "location",
        ):
            validate_single_line_field(name, getattr(args, name, None))
    except ValueError as exc:
        print(json.dumps({
            "success": False,
            "error": str(exc),
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    path = ssh_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    hosts = parse_hosts(read_lines(path))
    if any(args.alias in h.get("host_patterns", []) for h in hosts):
        print(json.dumps({
            "success": False,
            "error": f"Host alias already exists: {args.alias}",
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    backup = backup_config(path)
    tags = args.tags or ""
    block: list[str] = []
    if path.exists() and path.read_text(encoding="utf-8").strip():
        block.append("")
    if args.description:
        block.append(f"# description: {args.description}")
    if args.aliases:
        block.append(f"# aliases: {args.aliases}")
    if args.groups:
        block.append(f"# groups: {args.groups}")
    if tags:
        block.append(f"# tags: {tags}")
    if args.location:
        block.append(f"# location: {args.location}")
    block.extend([
        f"Host {args.alias}",
        f"    HostName {args.host}",
        f"    User {args.user}",
        f"    Port {args.port}",
    ])
    if args.key:
        block.append(f"    IdentityFile {args.key}")
    if args.proxy_jump:
        block.append(f"    ProxyJump {args.proxy_jump}")

    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(block))
        f.write("\n")

    print(json.dumps({
        "success": True,
        "alias": args.alias,
        "config_path": str(path),
        "backup_path": str(backup) if backup else None,
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage OpenSSH config hosts")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list")
    p_list.set_defaults(func=cmd_list)

    p_find = sub.add_parser("find", aliases=["search"])
    p_find.add_argument("query", nargs="+")
    p_find.set_defaults(func=cmd_find)

    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("query", nargs="+")
    p_resolve.set_defaults(func=cmd_resolve)

    p_show = sub.add_parser("show")
    p_show.add_argument("alias")
    p_show.set_defaults(func=cmd_show)

    p_add = sub.add_parser("add")
    p_add.add_argument("alias")
    p_add.add_argument("--host", required=True)
    p_add.add_argument("--user", required=True)
    p_add.add_argument("--port", default="22")
    p_add.add_argument("--key")
    p_add.add_argument("--proxy-jump")
    p_add.add_argument("--description")
    p_add.add_argument("--aliases")
    p_add.add_argument("--groups")
    p_add.add_argument("--tags")
    p_add.add_argument("--location")
    p_add.set_defaults(func=cmd_add)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
