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
    lines = text.splitlines(keepends=True)
    updated_lines = []
    in_project = False
    replaced = False

    for line in lines:
        stripped = line.lstrip("\ufeff").strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
        if in_project and stripped.startswith("version") and "=" in stripped and not replaced:
            indent = line[: len(line) - len(line.lstrip("\ufeff "))]
            line = f'{indent}version = "{version}"\n'
            replaced = True
        updated_lines.append(line)

    if not replaced:
        print("Failed to find version in plugin/pyproject.toml.")
        return 1

    updated = "".join(updated_lines)

    pyproject_path.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
