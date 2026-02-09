"""Watch `clipabit.py` and copy it to DaVinci Resolve Scripts/Utility on save.

Usage:
  python utils/plugins/davinci/watch_clipabit.py

Options / behavior:
- By default watches the workspace `frontend/plugin/clipabit.py` file and copies
  it into the target DaVinci Resolve Utility folder (path provided by the user).
- If `watchdog` is installed this uses an event-driven observer, otherwise falls
  back to a simple polling loop.

Run in PowerShell (from the repository root):

  python -m pip install watchdog  # optional but recommended
  python utils/plugins/davinci/watch_clipabit.py

You can stop with Ctrl+C.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import time
import platform
from pathlib import Path

logger = logging.getLogger("watch_clipabit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


DEFAULT_SOURCE = Path("plugin")
DEFAULT_SHIM_NAME = "ClipABit.py"
DEFAULT_PACKAGE_NAME = "clipabit"


def get_resolve_fusion_dir():
    """Returns the base Fusion path."""
    home = Path.home()
    system = platform.system()

    if system == "Windows":
        base_path = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return base_path / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion"
    elif system == "Darwin":
        return home / "Library" / "Application Support" / "Blackmagic Design" / "DaVinci Resolve" / "Fusion"
    else:
        return home / ".local" / "share" / "DaVinci Resolve" / "Fusion"


def get_resolve_paths():
    """Returns dictionary of relevant Resolve paths."""
    fusion = get_resolve_fusion_dir()
    return {
        "fusion": fusion,
        "utility": fusion / "Scripts" / "Utility",
        "modules": fusion / "Modules"
    }


def sync_plugin(src_root: Path, paths: dict):
    """Sync all plugin parts to Resolve."""
    # 1. Sync Shim
    shim_src = src_root / "clipabitshim.py"
    if shim_src.exists():
        dst_utility = paths["utility"]
        dst_utility.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shim_src, dst_utility / DEFAULT_SHIM_NAME)
        logger.info("Synced Shim: %s -> %s", shim_src.name, dst_utility / DEFAULT_SHIM_NAME)

    # 2. Sync Package
    pkg_src = src_root / "clipabit"
    if pkg_src.exists():
        dst_modules_parent = paths["modules"]
        dst_modules_parent.mkdir(parents=True, exist_ok=True)
        dst_modules = dst_modules_parent / "clipabit"
        if dst_modules.exists():
            shutil.rmtree(dst_modules)
        shutil.copytree(pkg_src, dst_modules)
        logger.info("Synced Package: %s -> %s", pkg_src.name, dst_modules)


def run_polling(src: Path, paths: dict, interval: float = 0.5):
    """Fallback polling loop."""
    logger.info("Polling started for %s", src)
    last_mtime = None
    while True:
        try:
            # Check for any change in the whole plugin tree
            mtime = 0
            for p in src.rglob("*"):
                if p.is_file():
                    mtime = max(mtime, p.stat().st_mtime)
            
            if mtime > (last_mtime or 0):
                last_mtime = mtime
                sync_plugin(src, paths)
        except Exception as e:
            logger.error("Polling error: %s", e)
        time.sleep(interval)


def run_watchdog(src: Path, paths: dict):
    """Use watchdog to observe the directory."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        return run_polling(src, paths)

    class Handler(FileSystemEventHandler):
        def __init__(self, src_root: Path, paths: dict):
            self.src_root = src_root.resolve()
            self.paths = paths
            self._last_sync = 0.0

        def on_any_event(self, event):
            if event.is_directory:
                return
            
            # Debounce
            now = time.time()
            if (now - self._last_sync) < 0.5:
                return
            
            # Check if it's inside our plugin dir
            try:
                event_path = Path(event.src_path).resolve()
                if self.src_root in event_path.parents or event_path == self.src_root:
                    self._last_sync = now
                    sync_plugin(self.src_root, self.paths)
            except Exception:
                pass

    handler = Handler(src, paths)
    observer = Observer()
    observer.schedule(handler, str(src.resolve()), recursive=True)
    observer.start()
    logger.info("Started watchdog observer for %s", src)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Watch ClipABit plugin folder and sync to Resolve")
    p.add_argument("--source", "-s", type=str, default=str(DEFAULT_SOURCE), help="Path to plugin/ folder")
    p.add_argument("--poll", action="store_true", help="Force polling mode")
    return p.parse_args()


def main():
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    src = (script_dir / args.source).resolve()
    
    if not src.exists():
        logger.error("Source directory not found: %s", src)
        return

    resolve_paths = get_resolve_paths()
    logger.info("Watching: %s", src)
    logger.info("Target Utility: %s", resolve_paths["utility"])
    logger.info("Target Modules: %s", resolve_paths["modules"])

    # Initial sync
    sync_plugin(src, resolve_paths)

    if args.poll:
        run_polling(src, resolve_paths)
    else:
        run_watchdog(src, resolve_paths)


if __name__ == "__main__":
    main()
