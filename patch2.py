import os
import re
from pathlib import Path

worker_path = Path(r"C:\MyProjects\SnapBeatServer\beatcanvas\worker.py")
with open(worker_path, "r", encoding="utf-8") as f:
    worker_code = f.read()

# Replace Worker.__init__
init_old = """class Worker:
    def __init__(self, job_store: store.JobStore):
        self.store = job_store
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancels: set[int] = set()
        self._lock = threading.Lock()
        self.current_job_id: int | None = None"""
init_new = """class Worker:
    def __init__(self, job_store: store.JobStore):
        self.store = job_store
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancels: set[int] = set()
        self._lock = threading.Lock()
        self.current_job_id: int | None = None
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._active_count = 0
        self._active_lock = threading.Lock()
        self._last_progress_commit = {}"""
if init_old in worker_code:
    worker_code = worker_code.replace(init_old, init_new)
else:
    print("WARNING: init_old not found")

# Replace _loop and _process_next
loop_old_pattern = r"    def _loop\(self\) -> None:.*?    def _process_next\(self\) -> None:.*?        self\.store\.update\(job\.id, status=store\.STATUS_FAILED, stage=\"failed\",\s+error=err\)"
loop_new = """    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                with self._active_lock:
                    can_start = self._active_count < 4
                while can_start:
                    job = self.store.next_queued()
                    if not job:
                        break
                    with self._active_lock:
                        self._active_count += 1
                        can_start = self._active_count < 4
                    self._executor.submit(self._process_job, job)
            except Exception:
                import traceback
                traceback.print_exc()
                import time
                time.sleep(2)
                continue

    def _process_job(self, job: store.Job) -> None:
        try:
            self._run(job)
        except Exception as exc:
            import traceback
            err = traceback.format_exc(limit=8)
            print(f"Job {job.id} failed:\\n{err}")
            self.store.update(job.id, status=store.STATUS_FAILED, stage="failed", error=err)
        finally:
            import shutil
            dropped_folder = Path(job.options.get("photo_folder", "")).parent
            if dropped_folder.exists() and "_dropped" in str(dropped_folder):
                shutil.rmtree(dropped_folder, ignore_errors=True)
            with self._active_lock:
                self._active_count -= 1
            self.notify()"""

worker_code = re.sub(loop_old_pattern, loop_new, worker_code, flags=re.DOTALL)

with open(worker_path, "w", encoding="utf-8") as f:
    f.write(worker_code)
print("worker.py patched concurrency")
