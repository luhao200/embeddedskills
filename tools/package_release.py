#!/usr/bin/env python3
import argparse
import hashlib
import re
import subprocess
import zipfile
from pathlib import Path


FIXED_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
EXCLUDED_PARTS = {".git", ".github", ".venv", "__pycache__", "tests"}


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def normalized_version(root: Path, requested: str | None) -> str:
    version = requested
    if not version:
        process = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        )
        version = process.stdout.strip()
    version = version.removeprefix("v")
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", version):
        raise ValueError(f"invalid release version: {version!r}")
    return version


def included(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return not any(part in EXCLUDED_PARTS for part in relative.parts)


def write_zip(archive: Path, root: Path, files: list[Path]) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(files, key=lambda item: item.as_posix()):
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, path.read_bytes())


def build_archives(root: Path, output_directory: Path, version: str) -> list[Path]:
    root = root.resolve()
    output_directory = output_directory.resolve()
    if output_directory == root:
        raise ValueError("release output directory must not be the repository root")

    output_directory.mkdir(parents=True, exist_ok=True)
    for stale_archive in output_directory.glob("embeddedskills-*.zip"):
        stale_archive.unlink()
    checksum_file = output_directory / "SHA256SUMS"
    if checksum_file.exists():
        checksum_file.unlink()

    skill_directories = sorted(
        path for path in root.iterdir() if path.is_dir() and (path / "SKILL.md").is_file()
    )
    runtime_files = [
        path for path in root.iterdir() if path.is_file() and included(path, root)
    ]
    for directory in [root / "docs", *skill_directories]:
        if directory.is_dir():
            runtime_files.extend(
                path
                for path in directory.rglob("*")
                if path.is_file() and included(path, root)
            )

    archives: list[Path] = []
    bundle = output_directory / f"embeddedskills-{version}.zip"
    write_zip(bundle, root, runtime_files)
    archives.append(bundle)

    for skill_directory in skill_directories:
        files = [root / "LICENSE"]
        files.extend(
            path
            for path in skill_directory.rglob("*")
            if path.is_file() and included(path, root)
        )
        archive = output_directory / f"embeddedskills-{skill_directory.name}-{version}.zip"
        write_zip(archive, root, files)
        archives.append(archive)
    return archives


def write_checksums(output_directory: Path, archives: list[Path]) -> Path:
    checksum_file = output_directory / "SHA256SUMS"
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(archives)
    ]
    checksum_file.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return checksum_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic release archives")
    parser.add_argument("--root", type=Path, default=repository_root())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--version")
    args = parser.parse_args()

    root = args.root.resolve()
    output = (args.output or root / "dist").resolve()
    version = normalized_version(root, args.version)
    archives = build_archives(root, output, version)
    checksum_file = write_checksums(output, archives)
    for path in [*archives, checksum_file]:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
