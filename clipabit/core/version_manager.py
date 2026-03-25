"""Persistent version tracking and self-update logic.

Stores the installed version in ~/.clipabit/version.json so it survives
plugin file replacements. On startup, compares against the latest GitHub
release and can download + apply updates from the repo zipball.
"""

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path


def _version_file_path() -> Path:
    """Return ~/.clipabit/version.json (always writable, survives updates)."""
    p = Path(os.path.expanduser("~")) / ".clipabit"
    p.mkdir(parents=True, exist_ok=True)
    return p / "version.json"


def load_installed_version(fallback_tag: str) -> str:
    """Read the persisted version tag, falling back to Config.RELEASE_TAG."""
    vf = _version_file_path()
    if vf.exists():
        try:
            data = json.loads(vf.read_text(encoding="utf-8"))
            tag = data.get("installed_version")
            if tag:
                return tag
        except (json.JSONDecodeError, OSError):
            pass
    # First run or corrupt file — seed from the compiled-in constant
    save_installed_version(fallback_tag)
    return fallback_tag


def save_installed_version(tag: str) -> None:
    """Persist the installed version tag to disk."""
    vf = _version_file_path()
    vf.write_text(json.dumps({"installed_version": tag}), encoding="utf-8")
    print(f"[Version] Saved installed version: {tag}")


def parse_semver(tag: str) -> tuple:
    """Parse 'v1.2.3' or 'v1.2.3-staging.4' into a comparable tuple.

    Returns (major, minor, patch, prerelease_order, pre_num) where
    prerelease_order sorts: release (99) > staging (1) > beta (0) > alpha (-1).
    A stable release like 'v1.2.0' sorts higher than 'v1.2.0-staging.5'.
    """
    clean = tag.lstrip("v")
    pre_part = None
    if "-" in clean:
        clean, pre_part = clean.split("-", 1)
    parts = clean.split(".")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0

    if pre_part is None:
        return (major, minor, patch, 99, 0)  # stable release ranks highest

    # pre_part like "staging.4", "alpha.1", "beta.2"
    pre_tokens = pre_part.split(".")
    label = pre_tokens[0]
    num = int(pre_tokens[1]) if len(pre_tokens) > 1 else 0
    order = {"alpha": -1, "beta": 0, "staging": 1}.get(label, -2)
    return (major, minor, patch, order, num)


def is_newer(remote_tag: str, local_tag: str) -> bool:
    """Return True if remote_tag is strictly newer than local_tag."""
    return parse_semver(remote_tag) > parse_semver(local_tag)


def get_plugin_install_dir() -> Path:
    """Detect the directory containing the installed 'clipabit' package.

    Returns the parent directory that holds the 'clipabit/' folder
    (i.e. the dir where 'clipabit.py' entry-point lives alongside 'clipabit/').
    """
    # This file is clipabit/core/version_manager.py  →  go up two levels
    core_dir = Path(__file__).resolve().parent          # clipabit/core/
    package_dir = core_dir.parent                       # clipabit/
    install_dir = package_dir.parent                    # parent that holds clipabit/
    return install_dir


def apply_update(zip_path: str, install_dir: Path) -> None:
    """Extract clipabit/ and clipabit.py from the GitHub zipball into install_dir.

    The zipball has a top-level folder like 'ClipABit-Resolve-Plugin-<sha>/'.
    We extract only the plugin files from it.
    """
    print(f"[Update] Extracting update from {zip_path}")
    print(f"[Update] Install directory: {install_dir}")

    with tempfile.TemporaryDirectory() as extract_dir:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        # Find the single top-level directory in the zipball
        top_entries = os.listdir(extract_dir)
        if len(top_entries) != 1:
            raise RuntimeError(f"Unexpected zipball structure: {top_entries}")
        repo_root = Path(extract_dir) / top_entries[0]

        # Verify expected files exist in the extracted content
        src_package = repo_root / "clipabit"
        src_entry = repo_root / "clipabit.py"
        if not src_package.is_dir():
            raise RuntimeError(f"clipabit/ not found in zipball at {src_package}")

        # Remove old package directory and replace
        dest_package = install_dir / "clipabit"
        if dest_package.exists():
            print(f"[Update] Removing old package: {dest_package}")
            shutil.rmtree(dest_package)
        print(f"[Update] Copying new package to {dest_package}")
        shutil.copytree(src_package, dest_package)

        # Replace entry-point script if present in zipball
        if src_entry.is_file():
            dest_entry = install_dir / "clipabit.py"
            print(f"[Update] Copying new entry point to {dest_entry}")
            shutil.copy2(src_entry, dest_entry)

        # Copy .env if present (contains config like Auth0 keys)
        src_env = repo_root / ".env"
        if src_env.is_file():
            dest_env = install_dir / ".env"
            if not dest_env.exists():
                print(f"[Update] Copying .env to {dest_env}")
                shutil.copy2(src_env, dest_env)

    print("[Update] Update applied successfully")
