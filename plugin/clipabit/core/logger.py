import os
import sys
import datetime
import threading

class Logger:
    """Simple file-based logger for debugging crashes."""
    
    _instance = None
    _lock = threading.Lock()
    
    @classmethod
    def setup(cls, log_dir):
        """Initialize the logger."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(log_dir)
            return cls._instance
            
    @classmethod
    def info(cls, message):
        if cls._instance:
            cls._instance._write("INFO", message)
        else:
            print(f"[INFO] {message}")
            
    @classmethod
    def error(cls, message):
        if cls._instance:
            cls._instance._write("ERROR", message)
        else:
            print(f"[ERROR] {message}")
            
    def __init__(self, log_dir):
        self.log_file = None
        try:
            self.log_file = os.path.join(log_dir, "clipabit_debug.log")
            # Ensure dir exists
            os.makedirs(log_dir, exist_ok=True)
            
            with open(self.log_file, "a") as f:
                f.write(f"\n=== Log Session Started: {datetime.datetime.now()} ===\n")
        except Exception as e:
            print(f"Failed to init log file at {log_dir}: {e}")
            # Fallback to tmp if possible
            try:
                import tempfile
                tmp_dir = tempfile.gettempdir()
                self.log_file = os.path.join(tmp_dir, "clipabit_fallback.log")
                with open(self.log_file, "a") as f:
                    f.write(f"\n=== Log Session Started (Fallback): {datetime.datetime.now()} ===\nOriginal error: {e}\n")
            except:
                self.log_file = None

    def _write(self, level, message):
        # Console output
        print(f"[{level}] {message}")
        
        # File output
        if self.log_file:
            try:
                timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                thread_name = threading.current_thread().name
                entry = f"{timestamp} [{thread_name}] [{level}] {message}\n"
                with open(self.log_file, "a") as f:
                    f.write(entry)
            except Exception:
                pass
