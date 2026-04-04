"""Persistent version tracking and self-update logic.

Stores the installed version in ~/.clipabit/version.json so it survives
plugin file replacements. On startup, compares against the latest GitHub
release and can download + apply updates from the repo zipball.
"""

import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path


def _version_file_path() -> Path:
    """Return ~/.clipabit/version.json (always writable, survives updates)."""
    p = Path(os.path.expanduser("~")) / ".clipabit"
    p.mkdir(parents=True, exist_ok=True)
    return p / "version.json"


def get_embedded_version() -> str | None:
    """Read the version string directly from pyproject.toml if available."""
    try:
        install_dir = get_plugin_install_dir()
        pyproject_path = install_dir / "pyproject.toml"
        if pyproject_path.exists():
            content = pyproject_path.read_text(encoding="utf-8")
            # Simple regex to find version = "..."
            match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
            if match:
                return match.group(1)
    except Exception as e:
        print(f"[Version] Error reading pyproject.toml: {e}")
    return None


def load_installed_version(fallback_tag: str) -> str:
    """Read the version. Prioritizes version.json, then pyproject.toml, then fallback.
    
    This ensures the version persisted in ~/.clipabit (which survives plugin file 
    replacements) is the authority, while still allowing fresh installs to 
    be seeded from the package files.
    """
    # 1. Try persisted version.json (~/.clipabit/version.json)
    vf = _version_file_path()
    if vf.exists():
        try:
            data = json.loads(vf.read_text(encoding="utf-8"))
            tag = data.get("installed_version")
            if tag:
                return tag
        except (json.JSONDecodeError, OSError):
            pass

    # 2. Try pyproject.toml (the source of truth for what's on disk)
    embedded = get_embedded_version()
    if embedded:
        # Seed version.json so it's the authority next time
        save_installed_version(embedded)
        return embedded

    # 3. Fallback to the provided constant (likely Config.RELEASE_TAG)
    save_installed_version(fallback_tag)
    return fallback_tag


def save_installed_version(tag: str) -> None:
    """Persist the installed version tag to disk."""
    vf = _version_file_path()
    try:
        # Only write if it's actually different to avoid unnecessary IO
        if vf.exists():
            data = json.loads(vf.read_text(encoding="utf-8"))
            if data.get("installed_version") == tag:
                return
    except Exception:
        pass

    vf.write_text(json.dumps({"installed_version": tag}), encoding="utf-8")
    print(f"[Version] Saved installed version: {tag}")


def parse_semver(tag: str) -> tuple:
    """Parse 'v1.2.3', 'v1.2.3-staging.4', or PEP 440 '1.3.0rc2' into a comparable tuple.

    Returns (major, minor, patch, prerelease_order, pre_num) where
    prerelease_order sorts: release (99) > staging/rc (1) > beta (0) > alpha (-1).
    """
    clean = tag.lstrip("v")

    # Handle PEP 440 pre-releases (e.g. 1.3.0rc2, 1.3rc2, 1.3.0a1)
    # Convert them to SemVer-ish format for the unified logic below
    pep_match = re.match(r"^(\d+\.\d+(?:\.\d+)?)(rc|a|b)(\d+)$", clean)
    if pep_match:
        base, label, num = pep_match.groups()
        label_map = {"rc": "staging", "a": "alpha", "b": "beta"}
        # Ensure base has 3 parts for consistent splitting later
        if base.count(".") == 1:
            base = f"{base}.0"
        clean = f"{base}-{label_map[label]}.{num}"

    pre_part = None
    if "-" in clean:
        clean, pre_part = clean.split("-", 1)

    parts = clean.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        major, minor, patch = 0, 0, 0

    if pre_part is None:
        return (major, minor, patch, 99, 0)

    # pre_part like "staging.4", "rc.1", "alpha.1"
    pre_tokens = pre_part.split(".")
    label = pre_tokens[0].lower()
    num = 0
    if len(pre_tokens) > 1:
        try:
            num = int(pre_tokens[1])
        except ValueError:
            pass
            
    # Map common labels to ordering
    order_map = {
        "alpha": -1, 
        "beta": 0, 
        "rc": 1, 
        "staging": 1, 
        "pre": 1
    }
    order = order_map.get(label, -2)
    return (major, minor, patch, order, num)


def is_newer(remote_tag: str, local_tag: str) -> bool:
    """Return True if remote_tag is strictly newer than local_tag."""
    return parse_semver(remote_tag) > parse_semver(local_tag)


def is_prerelease(tag: str) -> bool:
    """Return True if the version tag represents a pre-release (staging, alpha, beta)."""
    # parse_semver returns (major, minor, patch, order, num)
    # order 99 is stable, everything else is pre-release
    return parse_semver(tag)[3] < 99


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
    """Extract clipabit/, clipabit.py, and pyproject.toml from the GitHub zipball into install_dir.

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
        src_toml = repo_root / "pyproject.toml"
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

        # Replace pyproject.toml so the new version is reflected locally
        if src_toml.is_file():
            dest_toml = install_dir / "pyproject.toml"
            print(f"[Update] Copying new pyproject.toml to {dest_toml}")
            shutil.copy2(src_toml, dest_toml)

        # Copy .env if present (contains config like Auth0 keys)
        src_env = repo_root / ".env"
        if src_env.is_file():
            dest_env = install_dir / ".env"
            if not dest_env.exists():
                print(f"[Update] Copying .env to {dest_env}")
                shutil.copy2(src_env, dest_env)

    print("[Update] Update applied successfully")

