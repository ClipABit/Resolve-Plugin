from __future__ import annotations

import re
import sys
from pathlib import Path


def semver_to_pep440(version: str) -> str:
    """Convert a semver pre-release version to PEP 440 format.

    Examples:
        1.1.1-staging.1 -> 1.1.1rc1
        1.1.1-alpha.3   -> 1.1.1a3
        1.1.1-beta.2    -> 1.1.1b2
        1.1.1            -> 1.1.1  (no change)
    """
    m = re.match(r"^(\d+\.\d+\.\d+)-(.+)\.(\d+)$", version)
    if not m:
        return version
    base, tag, num = m.group(1), m.group(2), m.group(3)
    pep440_tag = {"alpha": "a", "beta": "b"}.get(tag, "rc")
    return f"{base}{pep440_tag}{num}"


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: bump_version.py <version>")
        return 1

    version = semver_to_pep440(sys.argv[1].strip())
    if not version:
        print("Version is empty.")
        return 1

    pyproject_path = Path("pyproject.toml")
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
