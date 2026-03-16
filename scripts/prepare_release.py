from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run_script(script_name: str, version: str) -> None:
    script_path = ROOT / "scripts" / script_name
    completed = subprocess.run(
        [sys.executable, str(script_path), version],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: prepare_release.py <version>")
        return 1

    version = sys.argv[1].strip()
    if not version:
        print("Version is empty.")
        return 1

    run_script("bump_version.py", version)
    run_script("build_release_zip.py", version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())