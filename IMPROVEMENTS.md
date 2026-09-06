# SnapBeat Server — Code Review & Improvement Plan

**Reviewed:** September 6, 2026  
**Codebase:** `C:\MyProjects\SnapBeatServer`  
**Stack:** Python · FastAPI · SQLite · OpenCV · FFmpeg

---

## Executive Summary

The server was originally built as a **local desktop tool** (bound to `127.0.0.1`), then adapted for mobile API access by binding to `0.0.0.0` and adding three new endpoints. This hybrid state has introduced **critical security vulnerabilities**, **performance bottlenecks**, and **storage leaks** that must be fixed before any public deployment.

---

## PRIORITY 1: Critical Security & Crash Fixes

### 1.1 Path Traversal in `/api/render/mobile` (CRITICAL)

**File:** `beatcanvas/app.py` — `render_mobile()`

**Problem:** Uploaded filenames (`audio.filename`, `p.filename`) are used directly in file paths without sanitization. A malicious client can send `filename = "../../run.py"` and overwrite arbitrary files on the host.

**Current (Dangerous):**
```python
audio_path = music_dir / audio.filename
```

**Fix:**
```python
import re
from pathlib import Path

def sanitize_filename(raw: str) -> str:
    name = Path(raw.replace("\\", "/")).name  # strip directory components
    clean = re.sub(r"[^\w\-.]", "_", name).strip()
    return clean or "unnamed"

audio_path = music_dir / sanitize_filename(audio.filename)
```

---

### 1.2 Async Event Loop Blocking (Performance Crash)

**File:** `beatcanvas/app.py` — `render_mobile()`

**Problem:** `render_mobile` is declared `async def` but performs synchronous blocking disk I/O (`shutil.copyfileobj`). During large uploads (60 photos), the entire server freezes — no status polling, no other requests.

**Fix (Quick):** Remove `async` keyword, let FastAPI run it in a threadpool automatically:
```python
@app.post("/api/render/mobile")
def render_mobile(   # <-- remove 'async'
    audio: UploadFile = File(...),
    photos: List[UploadFile] = File(...)
):
```

---

### 1.3 Invalid Options Causing Silent Failures

**File:** `beatcanvas/app.py` — `render_mobile()`

**Problem:** Two options are set to values that don't match what the worker expects:
- `"frame": "9:16"` -> `config.FRAME_SIZES` expects `"portrait"`, `"landscape"`, or `"square"`
- `"intensity": "accurate"` -> Worker expects a `float`, not a string

**Fix:**
```python
options = {
    ...
    "frame": "portrait",      # was "9:16"
    "intensity": 1.0,         # was "accurate"
    ...
}
```

Also add minimum photo validation:
```python
if len(photos) < 3:
    raise HTTPException(status_code=400, detail="At least 3 photos required")
```

---

### 1.4 Dangerous Desktop Endpoints Exposed to Network

**File:** `beatcanvas/app.py`

**Problem:** By binding to `0.0.0.0`, these local-only endpoints are now accessible to anyone on your Wi-Fi:
- `/api/browse` — Pops up native file picker dialogs on your PC
- `/api/open-folder` — Opens Windows Explorer folders
- `/api/reveal` — Reveals files in Explorer
- `/api/photos?folder=C:\` — Lists directories on your PC
- `/thumb?path=C:\...` — Reads arbitrary image files from your PC

**Fix:** Add a guard function that blocks these endpoints when the request comes from a non-loopback IP:
```python
from starlette.requests import Request

def require_localhost(request: Request):
    client_ip = request.client.host
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Local access only")
```

Apply this guard to all desktop-only endpoints.

---

### 1.5 CORS Misconfiguration

**File:** `beatcanvas/app.py`

**Problem:** `allow_origins=["*"]` combined with `allow_credentials=True` is insecure AND violates the CORS spec.

**Fix:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8772", "http://127.0.0.1:8772"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## PRIORITY 2: Storage & Job Lifecycle

### 2.1 Disk Space Exhaustion (No Cleanup)

**Problem:** Every mobile render creates a folder `_dropped/<uuid>/` with full copies of audio + photos. Rendered videos accumulate in `output/`. **Nothing is ever deleted.**

**Fix — Auto-cleanup after job completes (in worker.py):**
```python
import shutil

dropped_folder = Path(job.options.get("photo_folder", "")).parent
if dropped_folder.exists() and "_dropped" in str(dropped_folder):
    shutil.rmtree(dropped_folder, ignore_errors=True)
```

**Fix — Retention policy for output videos:**
```python
import time

def prune_old_outputs(max_age_hours=24):
    cutoff = time.time() - (max_age_hours * 3600)
    for f in config.OUTPUT_DIR.iterdir():
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
```

---

### 2.2 `/api/clear-drops` Race Condition

**Problem:** Calling `/api/clear-drops` wipes `_dropped/` entirely, instantly breaking any active or queued mobile render jobs.

**Fix:** Only delete folders belonging to completed/failed/cancelled jobs.

---

### 2.3 Partial Files on Cancellation

**Problem:** When a job is cancelled mid-render, the partially written `.mp4` file stays in `output/`.

**Fix:** In `worker.py`, after detecting cancellation, delete the partial output:
```python
if self._cancelled(job.id):
    if target.exists():
        target.unlink(missing_ok=True)
    self.store.update(job.id, status=store.STATUS_CANCELLED, stage="cancelled")
    return
```

---

## PRIORITY 3: Performance & Reliability

### 3.1 Enable SQLite WAL Mode

**File:** `beatcanvas/store.py`

**Problem:** Default rollback journal mode forces exclusive locks during writes. When the worker commits progress every 5 frames (~6x/second), it blocks all status polling HTTP requests.

**Fix:** Add these pragmas in `__init__`:
```python
self._conn.execute("PRAGMA journal_mode=WAL;")
self._conn.execute("PRAGMA busy_timeout=5000;")
```

Also add an index:
```sql
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
```

---

### 3.2 Debounce Progress Updates

**File:** `beatcanvas/worker.py`

**Problem:** Progress is committed to SQLite every 5 frames, causing excessive disk I/O.

**Fix:** Only commit progress at most once per second:
```python
import time

class Worker:
    def __init__(self, ...):
        self._last_progress_commit = 0.0

    def _progress_callback(self, job_id, frame, total):
        now = time.monotonic()
        if now - self._last_progress_commit >= 1.0:
            self.store.update(job_id, progress=frame/total, ...)
            self._last_progress_commit = now
```

---

### 3.3 Worker Thread Supervision (Auto-Restart)

**Problem:** If the worker thread crashes from an unhandled exception, it dies permanently. All future jobs are stuck forever.

**Fix:** Wrap the loop with auto-restart:
```python
def _loop(self):
    while not self._stop.is_set():
        try:
            self._wake.wait(timeout=2.0)
            self._wake.clear()
            self._process_next()
        except Exception:
            import traceback
            traceback.print_exc()
            time.sleep(2)
            continue  # restart the loop
```

---

### 3.4 Parallel Rendering (Multi-Threaded Worker)

**Problem:** Only one job renders at a time. Multiple mobile users must wait in a queue.

**Fix:** Use a `ThreadPoolExecutor`:
```python
from concurrent.futures import ThreadPoolExecutor

MAX_CONCURRENT_JOBS = 4  # tune based on CPU cores

class Worker:
    def __init__(self, store):
        self.store = store
        self._executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS)
        self._active_count = 0
```

---

### 3.5 Crash Loop Prevention in `_recover()`

**File:** `beatcanvas/store.py`

**Problem:** On server restart, `_recover()` automatically requeues crashed jobs. If a job crashed the server, it crashes again in an infinite loop.

**Fix:** Track retry count and mark as failed after 3 retries.

---

## PRIORITY 4: API Hardening for Production

### 4.1 Use UUIDs Instead of Sequential Job IDs

**Problem:** Job IDs are `1, 2, 3...`. Any user can guess other users' job IDs and download their videos (IDOR vulnerability).

**Fix:** Generate a random UUID token for each job and use that for status/download endpoints.

---

### 4.2 Add Rate Limiting

**Fix:** Use `slowapi`:
```bash
pip install slowapi
```
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/render/mobile")
@limiter.limit("5/hour")
def render_mobile(request: Request, ...):
    ...
```

---

### 4.3 Add File Size & Type Validation

```python
MAX_AUDIO_SIZE = 50 * 1024 * 1024   # 50 MB
MAX_PHOTO_SIZE = 20 * 1024 * 1024   # 20 MB
ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
```

---

### 4.4 Truncate Error Messages

**Problem:** `job.error` returns full Python tracebacks containing internal file paths.

**Fix:**
```python
return {
    ...
    "error": (job.error or "")[:200] if job.error else None
}
```

---

## PRIORITY 5: Code Quality

### 5.1 Fix `run.py` Browser URL
Open `http://127.0.0.1:{port}/` instead of `http://0.0.0.0:{port}/`.

### 5.2 Remove HEIC from Supported Extensions
OpenCV on Windows cannot read `.heic` files. Remove it from `IMAGE_EXTENSIONS` in `config.py`.

### 5.3 Download Endpoint Error Handling
```python
@app.get("/api/render/download/{job_id}")
def render_download(job_id: int):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != store.STATUS_DONE:
        raise HTTPException(status_code=409, detail="Job not finished yet")
    
    video_path = job.result.get("video")
    if not video_path or not Path(video_path).exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")
    
    return FileResponse(video_path, media_type="video/mp4", filename=f"snapbeat_{job_id}.mp4")
```

---

## Summary Table

| # | Issue | Severity | Effort |
|---|-------|----------|--------|
| 1.1 | Path traversal in file uploads | CRITICAL | 10 min |
| 1.2 | Async event loop blocking | CRITICAL | 5 min |
| 1.3 | Invalid frame/intensity options | CRITICAL | 2 min |
| 1.4 | Desktop endpoints exposed to network | CRITICAL | 30 min |
| 1.5 | CORS misconfiguration | HIGH | 5 min |
| 2.1 | No file/folder cleanup (disk exhaustion) | HIGH | 30 min |
| 2.2 | clear-drops race condition | HIGH | 15 min |
| 2.3 | Partial files on cancellation | MEDIUM | 10 min |
| 3.1 | Enable SQLite WAL mode | MEDIUM | 5 min |
| 3.2 | Debounce progress updates | MEDIUM | 15 min |
| 3.3 | Worker thread auto-restart | MEDIUM | 10 min |
| 3.4 | Parallel rendering support | MEDIUM | 1 hour |
| 3.5 | Crash loop prevention in recovery | MEDIUM | 15 min |
| 4.1 | UUID tokens for job access | LOW | 30 min |
| 4.2 | Rate limiting | LOW | 15 min |
| 4.3 | File size and type validation | LOW | 15 min |
| 4.4 | Truncate error messages | LOW | 5 min |
| 5.1 | Fix browser URL in run.py | TRIVIAL | 2 min |
| 5.2 | Remove HEIC from extensions | TRIVIAL | 1 min |
| 5.3 | Download endpoint error handling | TRIVIAL | 5 min |
