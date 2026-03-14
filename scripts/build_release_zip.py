from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

EXCLUDED_DIRS = {"__pycache__"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue

        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue

        if path.suffix in EXCLUDED_SUFFIXES:
            continue

        yield path


def build_archive(zip_path: Path, root: Path, shim_file: Path, package_dir: Path) -> None:
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(shim_file, arcname="clipabit.py")

        for package_file in iter_files(package_dir):
            archive.write(package_file, arcname=package_file.relative_to(root).as_posix())


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: build_release_zip.py <version>")
        return 1

    version = sys.argv[1].strip()
    if not version:
        print("Version is empty.")
        return 1

    root = Path(".").resolve()
    shim_file = root / "clipabit.py"
    package_dir = root / "clipabit"

    if not shim_file.exists():
        print("Missing clipabit.py")
        return 1
    if not package_dir.exists() or not package_dir.is_dir():
        print("Missing clipabit/ package directory")
        return 1

    release_dir = root / "release"
    release_dir.mkdir(parents=True, exist_ok=True)

    # Keep the release directory deterministic to avoid uploading stale assets.
    for stale_zip in release_dir.glob("clipabit*.zip"):
        stale_zip.unlink()

    latest_zip_path = release_dir / "clipabit.zip"
    versioned_zip_path = release_dir / f"clipabit-v{version}.zip"

    build_archive(latest_zip_path, root, shim_file, package_dir)
    build_archive(versioned_zip_path, root, shim_file, package_dir)

    print(f"Created {latest_zip_path} for release {version}")
    print(f"Created {versioned_zip_path} for release {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
