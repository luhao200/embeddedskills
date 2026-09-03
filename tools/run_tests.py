#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    test_directories = sorted(
        path
        for path in root.rglob("tests")
        if path.is_dir()
        and any(path.glob("test_*.py"))
        and ".git" not in path.parts
        and "__pycache__" not in path.parts
    )
    if not test_directories:
        print("No test directories found.")
        return 0

    failed = False
    for test_directory in test_directories:
        print(f"==> {test_directory.relative_to(root)}", flush=True)
        process = subprocess.run([
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(test_directory),
            "-p",
            "test_*.py",
            "-v",
        ], cwd=root)
        failed = failed or process.returncode != 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
