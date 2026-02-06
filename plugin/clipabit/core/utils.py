import sys
import os
import json
import hashlib
from pathlib import Path
from typing import Dict

import inspect

def get_storage_path() -> Path:
    """Get path for local storage, robust to Resolve environment."""
    try:
        # standard approach
        script_path = Path(__file__).resolve()
    except NameError:
        # Resolve 'script' environment
        try:
            script_path = Path(inspect.getfile(inspect.currentframe())).resolve()
        except Exception:
            # Fallback to user home if we can't find our own path
            # This ensures we always have a writable location
            user_home = Path(os.path.expanduser("~"))
            storage_dir = user_home / ".clipabit" / "localstorage"
            storage_dir.mkdir(parents=True, exist_ok=True)
            return storage_dir / "processed_files.json"

    # If we found the script path, store relative to it (but ensure parents exist)
    # We navigate up from plugin/clipabit/core/utils.py to plugin/clipabit/localstorage
    # script_path.parent = core
    # script_path.parent.parent = clipabit
    storage_dir = script_path.parent.parent / "localstorage"
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        # Fallback if package dir is read-only
        user_home = Path(os.path.expanduser("~"))
        storage_dir = user_home / ".clipabit" / "localstorage"
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

def get_hashed_identifier(filepath: str, namespace: str, filename: str) -> str:
    """Match backend identifier generation for plugin uploads."""
    identifier_source = filepath if filepath else f"{namespace}/{filename}"
    return hashlib.sha256(identifier_source.encode()).hexdigest()
