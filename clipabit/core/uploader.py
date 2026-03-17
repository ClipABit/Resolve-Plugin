import os
import traceback
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from ..api.config import Config


class FileUploader(QObject):
    """Non-blocking file uploader using Qt's network stack.

    Uses NetworkClient (QNetworkAccessManager) internally — the upload runs
    entirely on the Qt event loop with no threads or subprocesses.
    """

    upload_started = pyqtSignal(str)              # filename
    upload_success = pyqtSignal(str, str, dict)   # filename, file_hash, response_data
    upload_failed = pyqtSignal(str, str, str)     # filename, file_hash, error_message
    upload_progress = pyqtSignal(str, str)        # filename, status_message

    MAX_RETRIES = 3

    def __init__(self, file_info: dict, namespace: str, network=None, parent=None):
        super().__init__(parent)
        self.file_info = file_info
        self.namespace = namespace
        self.filepath = file_info["filepath"]
        self.filename = file_info["filename"]
        self.file_hash = file_info["hash"]
        self._network = network
        print(f"[Upload] FileUploader created: {self.filename} -> {namespace}")

    def start(self):
        """Begin the upload (returns immediately — non-blocking)."""
        if self._network is None:
            self.upload_failed.emit(
                self.filename, self.file_hash, "No NetworkClient configured"
            )
            return
        if not os.path.exists(self.filepath):
            print(f"[Upload] File not found: {self.filepath}")
            self.upload_failed.emit(
                self.filename, self.file_hash, f"File not found: {self.filepath}"
            )
            return

        file_size = os.path.getsize(self.filepath)
        print(f"[Upload] Starting non-blocking upload: {self.filename} "
              f"({file_size / (1024*1024):.1f} MB, hash={self.file_hash[:8]})")
        self.upload_started.emit(self.filename)
        self._upload(retry_count=0)

    def _upload(self, retry_count: int):
        file_size = os.path.getsize(self.filepath)
        file_size_mb = file_size / (1024 * 1024)
        timeout = min(600, max(60, int(file_size_mb * 60)))

        label = f"Uploading {self.filename}..."
        if retry_count > 0:
            label += f" (attempt {retry_count + 1})"
            print(f"[Upload] Retry {retry_count + 1}/{self.MAX_RETRIES} for {self.filename}")
        self.upload_progress.emit(self.filename, label)

        def on_success(status, data):
            try:
                if isinstance(data, dict) and data.get("job_id"):
                    job_id = data["job_id"]
                    print(f"[Upload] Success: {self.filename} -> job_id={job_id} (HTTP {status})")
                    self.upload_success.emit(self.filename, self.file_hash, data)
                else:
                    print(f"[Upload] No job_id in response: {data}")
                    self.upload_failed.emit(
                        self.filename, self.file_hash, f"No job ID returned: {data}"
                    )
            except Exception as e:
                print(f"[Upload] Error in success handler: {e}")
                traceback.print_exc()
                self.upload_failed.emit(self.filename, self.file_hash, str(e))

        def on_error(msg):
            is_network = msg.startswith("Network error:")
            print(f"[Upload] Error for {self.filename}: {msg[:200]} "
                  f"(network={is_network}, retry={retry_count}/{self.MAX_RETRIES})")
            if retry_count < self.MAX_RETRIES and is_network:
                wait = (2 ** retry_count) * 2
                print(f"[Upload] Will retry in {wait}s")
                self.upload_progress.emit(
                    self.filename, f"Network error, retrying in {wait}s..."
                )
                QTimer.singleShot(
                    wait * 1000, lambda: self._upload(retry_count + 1)
                )
            else:
                print(f"[Upload] Giving up on {self.filename}")
                self.upload_failed.emit(self.filename, self.file_hash, msg)

        def on_progress(sent, total):
            if total > 0:
                pct = int(sent / total * 100)
                self.upload_progress.emit(
                    self.filename, f"Uploading {self.filename}... {pct}%"
                )

        self._network.post_multipart(
            Config.UPLOAD_API_URL,
            fields={"namespace": self.namespace},
            file_path=self.filepath,
            filename=self.filename,
            timeout=timeout,
            on_success=on_success,
            on_error=on_error,
            on_progress=on_progress,
        )
