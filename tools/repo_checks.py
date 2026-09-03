#!/usr/bin/env python3
import argparse
import ast
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


FRONTMATTER_PATTERN = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
SKIP_DIRECTORIES = {".git", ".venv", "__pycache__", "dist"}


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def files_with_suffix(root: Path, suffix: str) -> list[Path]:
    return sorted(
        path
        for path in root.rglob(f"*{suffix}")
        if not any(part in SKIP_DIRECTORIES for part in path.parts)
    )


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        raise ValueError("缺少以 --- 包围的 frontmatter")

    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip()
    return values


def check_skill_metadata(root: Path) -> list[str]:
    errors: list[str] = []
    for path in files_with_suffix(root, "SKILL.md"):
        try:
            metadata = parse_frontmatter(path)
        except ValueError as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
            continue

        expected_name = path.parent.name
        if metadata.get("name") != expected_name:
            errors.append(
                f"{path.relative_to(root)}: name 应为 {expected_name!r}"
            )
        if not metadata.get("description"):
            errors.append(f"{path.relative_to(root)}: description 不能为空")
    return errors


def check_python_syntax(root: Path) -> list[str]:
    errors: list[str] = []
    for path in files_with_suffix(root, ".py"):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
    return errors


def check_json(root: Path) -> list[str]:
    errors: list[str] = []
    for path in files_with_suffix(root, ".json"):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
    return errors


def local_link_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1:target.index(">")]
    else:
        target = target.split(maxsplit=1)[0]

    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    return unquote(parsed.path)


def check_markdown_links(root: Path) -> list[str]:
    errors: list[str] = []
    for path in files_with_suffix(root, ".md"):
        text = path.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(text):
            target = local_link_target(match.group(1))
            if target is None:
                continue
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                errors.append(
                    f"{path.relative_to(root)}: 本地链接越出仓库范围: {target}"
                )
                continue
            if not resolved.exists():
                line = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{path.relative_to(root)}:{line}: 本地链接不存在: {target}"
                )
    return errors


def skill_directories(root: Path) -> list[str]:
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )


def check_i18n_entrypoints(root: Path) -> list[str]:
    errors: list[str] = []
    pairs = [
        (root / "README.md", root / "README.en.md"),
        (
            root / "docs" / "getting-started.md",
            root / "docs" / "getting-started.en.md",
        ),
    ]
    for chinese, english in pairs:
        if not chinese.is_file():
            errors.append(f"缺少中文入口: {chinese.relative_to(root)}")
        if not english.is_file():
            errors.append(f"缺少英文入口: {english.relative_to(root)}")

    if not all(path.is_file() for pair in pairs for path in pair):
        return errors

    chinese_readme = pairs[0][0].read_text(encoding="utf-8").casefold()
    english_readme = pairs[0][1].read_text(encoding="utf-8").casefold()
    for skill in skill_directories(root):
        if skill.casefold() not in chinese_readme:
            errors.append(f"README.md 未列出 skill: {skill}")
        if skill.casefold() not in english_readme:
            errors.append(f"README.en.md 未列出 skill: {skill}")
    return errors


def run_checks(root: Path) -> list[str]:
    errors: list[str] = []
    checks = [
        check_skill_metadata,
        check_python_syntax,
        check_json,
        check_markdown_links,
        check_i18n_entrypoints,
    ]
    for check in checks:
        current = check(root)
        print(f"{check.__name__}: {'PASS' if not current else 'FAIL'}")
        errors.extend(current)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the embeddedskills repository")
    parser.add_argument("--root", type=Path, default=repository_root())
    args = parser.parse_args()

    root = args.root.resolve()
    errors = run_checks(root)
    if errors:
        print("\n校验失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("\n全部仓库校验通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
