from PyQt6.QtCore import QThread, pyqtSignal
import requests
import os
import time
import traceback
from ..api.config import Config

class FileUploader(QThread):
    """Background thread to handle file uploads without blocking the UI."""
    
    # Signals to communicate with Main Thread
    upload_started = pyqtSignal(str)              # filename
    upload_success = pyqtSignal(str, str, dict)   # filename, file_hash, response_data
    upload_failed = pyqtSignal(str, str, str)     # filename, file_hash, error_message
    upload_progress = pyqtSignal(str, str)        # filename, status_message
    
    def __init__(self, file_info: dict, namespace: str):
        super().__init__()
        self.file_info = file_info
        self.namespace = namespace
        self.filepath = file_info['filepath']
        self.filename = file_info['filename']
        self.file_hash = file_info['hash']
        
    def run(self):
        """Execute the upload logic."""
        self.upload_started.emit(self.filename)
        self._upload_with_retry()
        
    def _upload_with_retry(self, retry_count=0, max_retries=3):
        """Internal upload logic with retries."""
        try:
            # File size check
            if not os.path.exists(self.filepath):
                self.upload_failed.emit(self.filename, self.file_hash, f"File not found: {self.filepath}")
                return

            file_size = os.path.getsize(self.filepath)
            file_size_mb = file_size / (1024 * 1024)
            
            # Prepare request
            files_data = [("files", (self.filename, open(self.filepath, 'rb'), "video/mp4"))]
            data = {"namespace": self.namespace}
            
            # Update status
            msg = f"Uploading {self.filename}..." if retry_count == 0 else f"Uploading {self.filename} (attempt {retry_count + 1})..."
            self.upload_progress.emit(self.filename, msg)
            
            # Session setup
            session = requests.Session()
            upload_timeout = min(600, max(60, int(file_size_mb * 60)))
            
            # Perform blocking request (safe here because we are in a background thread)
            response = session.post(
                Config.UPLOAD_API_URL, 
                files=files_data, 
                data=data, 
                timeout=upload_timeout
            )
            
            # Close file handle by ensuring the request consumes the file or we rely on 'files_data' causing a read.
            # NOTE: 'requests' does not automatically close opened files passed to it if you opened them.
            # We should explicity close the file. 
            files_data[0][1][1].close() 

            if response.status_code == 200:
                result = response.json()
                job_id = result.get("job_id")
                if job_id:
                    self.upload_success.emit(self.filename, self.file_hash, result)
                else:
                    self.upload_failed.emit(self.filename, self.file_hash, f"No job ID returned. Response: {result}")
            else:
                self.upload_failed.emit(self.filename, self.file_hash, f"HTTP {response.status_code}: {response.text}")

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if retry_count < max_retries:
                wait_time = (2 ** retry_count) * 2
                self.upload_progress.emit(self.filename, f"Network error, retrying in {wait_time}s...")
                time.sleep(wait_time)
                self._upload_with_retry(retry_count + 1, max_retries)
            else:
                self.upload_failed.emit(self.filename, self.file_hash, f"Network error after retries: {str(e)}")
        except Exception as e:
            self.upload_failed.emit(self.filename, self.file_hash, f"Unexpected error: {str(e)}")
            print(traceback.format_exc())
