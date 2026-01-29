from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: bump_version.py <version>")
        return 1

    version = sys.argv[1].strip()
    if not version:
        print("Version is empty.")
        return 1

    pyproject_path = Path("plugin/pyproject.toml")
    text = pyproject_path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'(?m)^version\\s*=\\s*"[^"]+"',
        f'version = "{version}"',
        text,
        count=1,
    )
    if count != 1:
        print("Failed to find version in plugin/pyproject.toml.")
        return 1

    pyproject_path.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
