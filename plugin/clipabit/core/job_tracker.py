from PyQt6.QtCore import QThread, pyqtSignal
import requests
import time
import traceback
from ..api.config import Config

# --- Background Job Tracker Thread ---
class JobTracker(QThread):
    """Background thread to track upload job status."""
    
    job_completed = pyqtSignal(str, dict)  # job_id, result
    job_failed = pyqtSignal(str, str)      # job_id, error
    
    def __init__(self):
        super().__init__()
        self.jobs_to_track = {}  # job_id -> job_info
        self.running = True
        
    def add_job(self, job_id: str, job_info: dict):
        """Add a job to track."""
        self.jobs_to_track[job_id] = job_info
        
    def run(self):
        """Main tracking loop."""
        while self.running:
            jobs_to_remove = []
            
            for job_id, job_info in self.jobs_to_track.items():
                try:
                    response = requests.get(Config.STATUS_API_URL, params={"job_id": job_id}, timeout=Config.STATUS_CHECK_TIMEOUT)
                    if response.status_code == 200:
                        data = response.json()
                        status = data.get("status", "processing")
                        
                        if status == "completed":
                            self.job_completed.emit(job_id, data)
                            jobs_to_remove.append(job_id)
                        elif status == "failed":
                            error = data.get("error", "Unknown error")
                            self.job_failed.emit(job_id, error)
                            jobs_to_remove.append(job_id)
                        
                except Exception as e:
                    error_msg = f"Error checking job {job_id}: {e}\n{traceback.format_exc()}"
                    print(error_msg)
                    
            # Remove completed/failed jobs
            for job_id in jobs_to_remove:
                del self.jobs_to_track[job_id]
                
            time.sleep(Config.STATUS_CHECK_INTERVAL)
            
    def stop(self):
        """Stop the tracking thread."""
        self.running = False
