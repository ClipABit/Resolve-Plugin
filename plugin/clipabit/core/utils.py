import sys
import os
import json
import hashlib
from pathlib import Path
from typing import Dict

def get_storage_path() -> Path:
    """Get path for local storage."""
    try:
        # In a normal Python script
        script_path = Path(__file__).resolve()
    except Exception:
        # Fallback or when frozen/embedded
        script_arg = sys.argv[0] if len(sys.argv) > 0 else ""
        if script_arg:
            script_path = Path(script_arg)
            if not script_path.is_absolute():
                script_path = (Path.cwd() / script_arg).resolve()
        else:
            script_path = Path.cwd()
    
    # We want to store data relative to the package or user home
    # For simplicity, let's store it in a standard location or relative to the parent plugin folder
    # If this is inside clipabit/core/utils.py, parent.parent.parent is the root
    
    # Actually, the original logic put it relative to the script
    # We should probably use a user directory for robustness
    # But to match original behavior logic:
    return script_path.parent / "localstorage" / "processed_files.json"

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
