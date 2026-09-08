"""SnapBeat API Gateway — routes mobile requests across multiple render servers.

Usage:
    SNAPBEAT_BACKENDS=http://127.0.0.1:8772 python gateway.py

    Optional:
    SNAPBEAT_SERVERLESS_URL=https://snapbeat-serverless-xxx.asia-south1.run.app
    SNAPBEAT_INTERNAL_SECRET=xxx
    SNAPBEAT_GATEWAY_PORT=8000
"""
from __future__ import annotations

import os
import re
import sys
import time
import threading
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from typing import List

app = FastAPI(title="SnapBeat Gateway", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Configuration -----------------------------------------------------------

BACKENDS = [
    url.strip().rstrip("/") for url in
    os.environ.get("SNAPBEAT_BACKENDS", "http://127.0.0.1:8772").split(",")
    if url.strip()
]
SERVERLESS_URL = os.environ.get("SNAPBEAT_SERVERLESS_URL", "").strip().rstrip("/")
INTERNAL_SECRET = os.environ.get("SNAPBEAT_INTERNAL_SECRET", "").strip()
PORT = int(os.environ.get("PORT", os.environ.get("SNAPBEAT_GATEWAY_PORT", "8000")))
HEALTH_INTERVAL = 10  # seconds between health checks

# -- Backend health tracking -------------------------------------------------

# {backend_url: {"available": int, "status": "ok"|"down", "last_check": float}}
_backend_health: dict[str, dict] = {}
_health_lock = threading.Lock()


def _poll_health():
    """Background thread: poll each backend's /api/health every HEALTH_INTERVAL seconds."""
    while True:
        for url in BACKENDS:
            try:
                r = httpx.get(f"{url}/api/health", timeout=5.0)
                data = r.json()
                is_ok = data.get("ok", False) or data.get("status") == "ok"
                avail = data.get("available", 4 if data.get("worker_job") is None else 1) if is_ok else 0
                with _health_lock:
                    _backend_health[url] = {
                        "available": avail,
                        "active_jobs": data.get("active_jobs", 1 if data.get("worker_job") else 0),
                        "max_workers": data.get("max_workers", 4),
                        "status": "ok" if is_ok else "down",
                        "last_check": time.time(),
                    }
            except Exception:
                with _health_lock:
                    _backend_health[url] = {
                        "available": 0,
                        "active_jobs": 0,
                        "max_workers": 0,
                        "status": "down",
                        "last_check": time.time(),
                    }
        time.sleep(HEALTH_INTERVAL)


threading.Thread(target=_poll_health, daemon=True).start()


def _pick_backend() -> str | None:
    """Choose the backend with the most available capacity."""
    with _health_lock:
        candidates = [
            (url, info) for url, info in _backend_health.items()
            if info["status"] == "ok" and info["available"] > 0
        ]
        if not candidates and BACKENDS and not _backend_health:
            return BACKENDS[0]

    if not candidates:
        return None
    # Sort by most available slots
    candidates.sort(key=lambda x: x[1]["available"], reverse=True)
    return candidates[0][0]


def _backend_index(url: str) -> int:
    """Get the index of a backend URL for job ID encoding."""
    try:
        return BACKENDS.index(url)
    except ValueError:
        return 0


def _resolve_job_id(composite_id: str) -> tuple[str, int]:
    """Parse a composite job ID like '0_42' into (backend_url, local_job_id).

    Format: <backend_index>_<local_id>  or  s_<local_id>  for serverless.
    """
    parts = composite_id.split("_", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="invalid job ID format")

    prefix, local_id_str = parts
    try:
        local_id = int(local_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid job ID format")

    if prefix == "s":
        if not SERVERLESS_URL:
            raise HTTPException(status_code=400, detail="serverless not configured")
        return SERVERLESS_URL, local_id
    else:
        try:
            idx = int(prefix)
            if idx < 0 or idx >= len(BACKENDS):
                raise ValueError
            return BACKENDS[idx], local_id
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid backend index")


# -- Web Static Mounting -----------------------------------------------------
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if not WEB_DIR.is_dir():
    WEB_DIR = Path(__file__).resolve().parent / "web"

if WEB_DIR.is_dir():
    app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

# -- Endpoints ---------------------------------------------------------------

@app.get("/")
def gateway_index():
    if WEB_DIR.is_dir():
        return RedirectResponse(url="/web/")
    return {
        "service": "SnapBeat Gateway",
        "status": "online",
        "version": "1.1.0",
        "backends_configured": len(BACKENDS),
        "serverless_enabled": bool(SERVERLESS_URL),
    }


@app.post("/api/render/mobile")
async def render_mobile(request: Request):
    """Forward the render request.

    - render_type=instant: Routed to Serverless Cloud Run (protected with internal secret)
    - render_type=free_queue: Routed to VPS Local Backend (queued render, 0 credits)
    """
    body = await request.body()
    content_type = request.headers.get("content-type", "")

    # Inspect multipart body for render_type
    is_instant = False
    match_render_type = re.search(rb'name=[\"\']render_type[\"\']\r?\n\r?\n([^\r\n]+)', body[:4096])
    if match_render_type:
        val = match_render_type.group(1).decode("utf-8", errors="ignore").strip().lower()
        if val == "instant":
            is_instant = True

    backend = None
    prefix = "0"
    headers_to_send = {"content-type": content_type}

    if is_instant and SERVERLESS_URL:
        # Route to Serverless Cloud Run with security secret
        backend = SERVERLESS_URL
        prefix = "s"
        if INTERNAL_SECRET:
            headers_to_send["X-Serverless-Auth"] = INTERNAL_SECRET
    else:
        # Route to VPS backend queue
        backend = _pick_backend()
        if backend is None:
            if BACKENDS:
                backend = BACKENDS[0]
                prefix = str(_backend_index(backend))
            else:
                raise HTTPException(status_code=503, detail="all render servers are busy, please try again shortly")
        else:
            prefix = str(_backend_index(backend))

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{backend}/api/render/mobile",
                content=body,
                headers=headers_to_send,
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"backend error: {resp.text}")
        data = resp.json()
        local_id = data.get("job_id")
        return {"job_id": f"{prefix}_{local_id}"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"backend error: {exc}")


@app.get("/api/render/status/{composite_id}")
async def render_status(composite_id: str):
    """Route status check to the correct backend."""
    backend_url, local_id = _resolve_job_id(composite_id)
    headers_to_send = {}
    if composite_id.startswith("s_") and INTERNAL_SECRET:
        headers_to_send["X-Serverless-Auth"] = INTERNAL_SECRET

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{backend_url}/api/render/status/{local_id}", headers=headers_to_send)
        return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"backend error: {exc}")


@app.get("/api/render/download/{composite_id}")
async def render_download(composite_id: str, delete_after: bool = False):
    """Proxy the video download from the correct backend."""
    backend_url, local_id = _resolve_job_id(composite_id)
    headers_to_send = {}
    if composite_id.startswith("s_") and INTERNAL_SECRET:
        headers_to_send["X-Serverless-Auth"] = INTERNAL_SECRET

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.get(
                f"{backend_url}/api/render/download/{local_id}?delete_after={'true' if delete_after else 'false'}",
                headers=headers_to_send,
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="download failed")
        return Response(
            content=resp.content,
            media_type="video/mp4",
            headers={
                "content-disposition": f'attachment; filename="snapbeat_{composite_id}.mp4"',
                "content-length": str(len(resp.content)),
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"backend error: {exc}")


@app.post("/api/render/cleanup/{composite_id}")
async def render_cleanup(composite_id: str):
    """Forward cleanup request to the backend that processed this job."""
    backend_url, local_id = _resolve_job_id(composite_id)
    headers_to_send = {}
    if composite_id.startswith("s_") and INTERNAL_SECRET:
        headers_to_send["X-Serverless-Auth"] = INTERNAL_SECRET

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{backend_url}/api/render/cleanup/{local_id}", headers=headers_to_send)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="cleanup failed")
        return resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"backend error: {exc}")


@app.get("/api/health")
def gateway_health():
    """Aggregate health status from all backends."""
    with _health_lock:
        backends_info = []
        total_available = 0
        for url in BACKENDS:
            info = _backend_health.get(url, {"status": "unknown", "available": 0})
            backends_info.append({"url": url, **info})
            if info.get("status") == "ok":
                total_available += info.get("available", 0)

    return {
        "status": "ok",
        "backends": backends_info,
        "total_available": total_available,
        "serverless_configured": bool(SERVERLESS_URL),
    }


if __name__ == "__main__":
    print(f"SnapBeat Gateway listening on port {PORT}")
    print(f"Configured Backends: {BACKENDS}")
    if SERVERLESS_URL:
        print(f"Serverless Instant Backend: {SERVERLESS_URL}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

