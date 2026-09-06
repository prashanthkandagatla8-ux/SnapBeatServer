import os
import re
from pathlib import Path

root = Path(r"C:\MyProjects\SnapBeatServer\beatcanvas")

# -- Patch worker.py --
worker_path = root / "worker.py"
with open(worker_path, "r", encoding="utf-8") as f:
    worker_code = f.read()

# Add imports for threadpool and time
if "from concurrent.futures import ThreadPoolExecutor" not in worker_code:
    worker_code = worker_code.replace("import threading\n", "import threading\nimport time\nimport shutil\nfrom concurrent.futures import ThreadPoolExecutor\n")

# Replace Worker.__init__
init_old = """    def __init__(self, job_store: store.JobStore):
        self.store = job_store
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="beatcanvas-worker", daemon=True)
        self._cancel_flags: dict[int, bool] = {}"""
init_new = """    def __init__(self, job_store: store.JobStore):
        self.store = job_store
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancel_flags: dict[int, bool] = {}
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._active_count = 0
        self._active_lock = threading.Lock()
        self._last_progress_commit = {}
        self._thread = threading.Thread(target=self._loop, name="beatcanvas-worker", daemon=True)"""
worker_code = worker_code.replace(init_old, init_new)

# Replace _loop
loop_old = """    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=2.0)
            self._wake.clear()
            self._process_next()"""
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
                time.sleep(2)
                continue"""
worker_code = worker_code.replace(loop_old, loop_new)

# Replace _process_next
proc_old = """    def _process_next(self) -> None:
        job = self.store.next_queued()
        if not job:
            return

        try:
            self._run(job)
        except Exception as exc:
            import traceback
            err = traceback.format_exc(limit=8)
            print(f"Job {job.id} failed:\n{err}")
            self.store.update(job.id, status=store.STATUS_FAILED, stage="failed",
                              error=err)"""
proc_new = """    def _process_job(self, job) -> None:
        try:
            self._run(job)
        except Exception as exc:
            import traceback
            err = traceback.format_exc(limit=8)
            print(f"Job {job.id} failed:\n{err}")
            self.store.update(job.id, status=store.STATUS_FAILED, stage="failed", error=err)
        finally:
            # Cleanup dropped folder
            dropped_folder = Path(job.options.get("photo_folder", "")).parent
            if dropped_folder.exists() and "_dropped" in str(dropped_folder):
                shutil.rmtree(dropped_folder, ignore_errors=True)
            with self._active_lock:
                self._active_count -= 1
            self.notify()"""
worker_code = worker_code.replace(proc_old, proc_new)

# Update _run for progress debounce and cancel cleanup
progress_old = """        def progress(current: int, total: int, message: str) -> None:
            self.store.update(job.id, progress=current / max(1, total),
                              frame_current=current, frame_total=total,
                              stage=f"rendering frame {current} of {total}")"""
progress_new = """        def progress(current: int, total: int, message: str) -> None:
            now = time.monotonic()
            if now - self._last_progress_commit.get(job.id, 0.0) >= 1.0 or current == total:
                self.store.update(job.id, progress=current / max(1, total),
                                  frame_current=current, frame_total=total,
                                  stage=f"rendering frame {current} of {total}")
                self._last_progress_commit[job.id] = now"""
worker_code = worker_code.replace(progress_old, progress_new)

cancel_old = """        if self._cancelled(job.id):
            self._clear_cancel(job.id)
            self.store.update(job.id, status=store.STATUS_CANCELLED,
                              stage="cancelled", progress=0.0)
            return"""
cancel_new = """        if self._cancelled(job.id):
            if target.exists():
                target.unlink(missing_ok=True)
            self._clear_cancel(job.id)
            self.store.update(job.id, status=store.STATUS_CANCELLED,
                              stage="cancelled", progress=0.0)
            return"""
worker_code = worker_code.replace(cancel_old, cancel_new)

with open(worker_path, "w", encoding="utf-8") as f:
    f.write(worker_code)
print("worker.py patched")

# -- Patch app.py --
app_path = root / "app.py"
with open(app_path, "r", encoding="utf-8") as f:
    app_code = f.read()

# Replace mobile endpoint
render_mobile_old = """@app.post("/api/render/mobile")
async def render_mobile(
    audio: UploadFile = File(...),
    photos: List[UploadFile] = File(...)
):
    import shutil
    import uuid

    job_id_str = str(uuid.uuid4())
    inbox = config.ROOT / "_dropped" / job_id_str
    photos_dir = inbox / "photos"
    music_dir = inbox / "music"
    photos_dir.mkdir(parents=True, exist_ok=True)
    music_dir.mkdir(parents=True, exist_ok=True)

    # Save audio
    audio_path = music_dir / audio.filename
    with open(audio_path, "wb") as f:
        shutil.copyfileobj(audio.file, f)

    # Save photos
    photo_paths = []
    for p in photos:
        p_path = photos_dir / p.filename
        with open(p_path, "wb") as f:
            shutil.copyfileobj(p.file, f)
        photo_paths.append(str(p_path.resolve()))
        
    options = {
        "photo_folder": str(photos_dir.resolve()),
        "photo_order": photo_paths,
        "music_path": str(audio_path.resolve()),
        "fill": "repeat",
        "reading": "auto",
        "look": "mix",
        "frame": "9:16",
        "pace": "accurate",
        "intensity": "accurate",
        "cover_mode": "zoom",
        "reveal_shape": "circle",
    }

    job = job_store.create(f"mobile_{job_id_str[:8]}", AUTO_TEMPLATE, options)
    worker.notify()
    return {"job_id": job.id}"""

render_mobile_new = """@app.post("/api/render/mobile")
def render_mobile(
    audio: UploadFile = File(...),
    photos: List[UploadFile] = File(...)
):
    import shutil
    import uuid
    import re
    from pathlib import Path

    if len(photos) < 3:
        raise HTTPException(status_code=400, detail="At least 3 photos required")

    def sanitize_filename(raw: str) -> str:
        name = Path(raw.replace("\\\\", "/")).name
        clean = re.sub(r"[^\\w\\-.]", "_", name).strip()
        return clean or "unnamed"

    job_id_str = str(uuid.uuid4())
    inbox = config.ROOT / "_dropped" / job_id_str
    photos_dir = inbox / "photos"
    music_dir = inbox / "music"
    photos_dir.mkdir(parents=True, exist_ok=True)
    music_dir.mkdir(parents=True, exist_ok=True)

    # Save audio
    audio_path = music_dir / sanitize_filename(audio.filename)
    with open(audio_path, "wb") as f:
        shutil.copyfileobj(audio.file, f)

    # Save photos
    photo_paths = []
    for p in photos:
        p_path = photos_dir / sanitize_filename(p.filename)
        with open(p_path, "wb") as f:
            shutil.copyfileobj(p.file, f)
        photo_paths.append(str(p_path.resolve()))
        
    options = {
        "photo_folder": str(photos_dir.resolve()),
        "photo_order": photo_paths,
        "music_path": str(audio_path.resolve()),
        "fill": "repeat",
        "reading": "auto",
        "look": "mix",
        "frame": "portrait",
        "pace": "accurate",
        "intensity": 1.0,
        "cover_mode": "zoom",
        "reveal_shape": "circle",
    }

    job = job_store.create(f"mobile_{job_id_str[:8]}", AUTO_TEMPLATE, options)
    worker.notify()
    return {"job_id": job.id}"""
app_code = app_code.replace(render_mobile_old, render_mobile_new)

with open(app_path, "w", encoding="utf-8") as f:
    f.write(app_code)
print("app.py patched")
