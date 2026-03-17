import traceback
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from ..api.config import Config


class JobTracker(QObject):
    """Polls upload-job status using Qt's network stack.

    Uses an internal QTimer + NetworkClient — no threads, no subprocesses.
    """

    job_completed = pyqtSignal(str, dict)  # job_id, result
    job_failed = pyqtSignal(str, str)      # job_id, error

    def __init__(self, network=None, parent=None):
        super().__init__(parent)
        self._network = network
        self._jobs = {}       # job_id -> job_info
        self._in_flight = set()  # job_ids with pending requests
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        print("[JobTracker] Initialized (QTimer + NetworkClient, no threads)")

    # ── public API ───────────────────────────────────────────────────

    def start(self):
        """Start polling (just starts the internal timer)."""
        if self._network is None:
            print("[JobTracker] Cannot start — no NetworkClient configured")
            return
        interval = int(Config.STATUS_CHECK_INTERVAL * 1000)
        self._timer.start(interval)
        print(f"[JobTracker] Polling started (every {Config.STATUS_CHECK_INTERVAL}s)")

    def stop(self):
        """Stop polling."""
        self._timer.stop()
        print("[JobTracker] Polling stopped")

    def add_job(self, job_id: str, job_info: dict):
        """Register a job to track."""
        self._jobs[job_id] = job_info
        filename = job_info.get("filename", "?")
        print(f"[JobTracker] Tracking job {job_id} for {filename} "
              f"(total tracked: {len(self._jobs)})")

    @property
    def tracked_jobs(self):
        """Snapshot of currently tracked jobs."""
        return dict(self._jobs)

    # ── internals ────────────────────────────────────────────────────

    def _poll(self):
        if not self._jobs:
            return
        pending = [jid for jid in self._jobs if jid not in self._in_flight]
        if pending:
            print(f"[JobTracker] Polling {len(pending)} job(s), "
                  f"{len(self._in_flight)} already in-flight")
        for job_id in pending:
            self._in_flight.add(job_id)
            self._check(job_id)

    def _check(self, job_id: str):
        def on_success(status, data):
            self._in_flight.discard(job_id)
            if job_id not in self._jobs:
                print(f"[JobTracker] Job {job_id} already removed, ignoring response")
                return
            if not isinstance(data, dict):
                print(f"[JobTracker] Unexpected response for {job_id}: {type(data)}")
                return
            s = data.get("status", "processing")
            filename = self._jobs.get(job_id, {}).get("filename", "?")
            if s == "completed":
                print(f"[JobTracker] Job {job_id} COMPLETED for {filename}")
                self._jobs.pop(job_id, None)
                try:
                    self.job_completed.emit(job_id, data)
                except Exception as e:
                    print(f"[JobTracker] Error emitting job_completed: {e}")
                    traceback.print_exc()
            elif s == "failed":
                error = data.get("error", "Unknown error")
                print(f"[JobTracker] Job {job_id} FAILED for {filename}: {error}")
                self._jobs.pop(job_id, None)
                try:
                    self.job_failed.emit(job_id, error)
                except Exception as e:
                    print(f"[JobTracker] Error emitting job_failed: {e}")
                    traceback.print_exc()
            else:
                # Still processing — will check again on next poll cycle
                pass

        def on_error(msg):
            self._in_flight.discard(job_id)
            print(f"[JobTracker] Error checking job {job_id}: {msg[:200]}")

        self._network.get(
            Config.STATUS_API_URL,
            params={"job_id": job_id},
            timeout=Config.STATUS_CHECK_TIMEOUT,
            on_success=on_success,
            on_error=on_error,
        )
