from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
SHIM_SOURCE = ROOT / "clipabit.py"
PACKAGE_SOURCE = ROOT / "clipabit"
README_SOURCE = ROOT / "README.md"
ARCHIVE_PREFIX = "clipabit-plugin-"
ARCHIVE_ROOT = Path("ClipABit")


def write_file(archive: ZipFile, source_path: Path, archive_path: Path) -> None:
    archive.write(source_path, archive_path.as_posix())


def add_package_tree(archive: ZipFile) -> None:
    for source_path in sorted(PACKAGE_SOURCE.rglob("*")):
        if source_path.is_dir() or source_path.name == "__pycache__":
            continue
        if any(part == "__pycache__" for part in source_path.parts):
            continue
        archive_path = ARCHIVE_ROOT / "Modules" / "clipabit" / source_path.relative_to(PACKAGE_SOURCE)
        write_file(archive, source_path, archive_path)


def clean_previous_archives() -> None:
    if not DIST_DIR.exists():
        return

    for existing_archive in DIST_DIR.glob(f"{ARCHIVE_PREFIX}*.zip"):
        existing_archive.unlink()


def build_archive(version: str) -> Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    clean_previous_archives()

    archive_path = DIST_DIR / f"{ARCHIVE_PREFIX}{version}.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        write_file(archive, SHIM_SOURCE, ARCHIVE_ROOT / "Scripts" / "Utility" / "ClipABit.py")
        add_package_tree(archive)
        write_file(archive, README_SOURCE, ARCHIVE_ROOT / "README.md")
    return archive_path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: build_release_zip.py <version>")
        return 1

    version = sys.argv[1].strip()
    if not version:
        print("Version is empty.")
        return 1

    if not SHIM_SOURCE.is_file():
        print(f"Missing shim source: {SHIM_SOURCE}")
        return 1
    if not PACKAGE_SOURCE.is_dir():
        print(f"Missing package source: {PACKAGE_SOURCE}")
        return 1

    archive_path = build_archive(version)
    print(archive_path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
