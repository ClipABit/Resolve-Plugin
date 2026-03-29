import os
import json
import hashlib
import platform
from pathlib import Path
from typing import Dict


def get_storage_path() -> Path:
    """Get path for processed files — uses platform app data dir (survives updates)."""
    system = platform.system()
    if system == "Windows":
        base = os.getenv("APPDATA") or str(Path.home())
        storage_dir = Path(base) / "ClipABit"
    elif system == "Darwin":
        storage_dir = Path.home() / "Library" / "Application Support" / "ClipABit"
    else:
        xdg = os.getenv("XDG_CONFIG_HOME")
        base = xdg if xdg else str(Path.home() / ".config")
        storage_dir = Path(base) / "clipabit"

    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir / "processed_files.json"

def load_processed_files(storage_path: Path = None) -> Dict[str, Dict]:
    """Load list of processed files from local storage."""
    if storage_path is None:
        storage_path = get_storage_path()
        
    try:
        if storage_path.exists():
            with open(storage_path, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading processed files: {e}")
    return {}

def save_processed_files(data: Dict, storage_path: Path = None):
    """Save processed files to local storage."""
    if storage_path is None:
        storage_path = get_storage_path()
        
    try:
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(storage_path, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving processed files: {e}")

def get_file_hash(filepath: str) -> str:
    """Generate hash for file to track changes."""
    try:
        stat = os.stat(filepath)
        # Use file path, size, and modification time for hash
        content = f"{filepath}:{stat.st_size}:{stat.st_mtime}"
        return hashlib.md5(content.encode()).hexdigest()
    except Exception:
        return hashlib.md5(filepath.encode()).hexdigest()

def get_hashed_identifier(filepath: str) -> str:
    """SHA-256 hash of a file's content. Used as the backend video identifier."""
    if not filepath:
        raise ValueError("get_hashed_identifier called with empty filepath")
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
