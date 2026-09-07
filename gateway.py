"""SnapBeat API Gateway — routes mobile requests across multiple render servers.

Usage:
    SNAPBEAT_BACKENDS=http://vps1:8772,http://vps2:8772 python gateway.py

    Optional:
    SNAPBEAT_SERVERLESS_URL=https://snapbeat-xxx.run.app  (fallback when all busy)
    SNAPBEAT_GATEWAY_PORT=80
"""
from __future__ import annotations

import os
import sys
import time
import threading
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from typing import List

app = FastAPI(title="SnapBeat Gateway")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Configuration -----------------------------------------------------------

BACKENDS = [
    url.strip() for url in
    os.environ.get("SNAPBEAT_BACKENDS", "http://localhost:8772").split(",")
    if url.strip()
]
SERVERLESS_URL = os.environ.get("SNAPBEAT_SERVERLESS_URL", "")
PORT = int(os.environ.get("SNAPBEAT_GATEWAY_PORT", "80"))
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
                with _health_lock:
                    _backend_health[url] = {
                        "available": data.get("available", 0),
                        "active_jobs": data.get("active_jobs", 0),
                        "max_workers": data.get("max_workers", 4),
                        "status": "ok",
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


# -- Gateway Endpoints -------------------------------------------------------

@app.post("/api/render/mobile")
async def render_mobile(request: Request):
    """Forward the render request to the least-busy backend, or serverless fallback."""
    backend = _pick_backend()

    # Fallback to serverless if all VPS servers are busy
    if backend is None and SERVERLESS_URL:
        backend = SERVERLESS_URL
        prefix = "s"
    elif backend is None:
        raise HTTPException(status_code=503, detail="all servers are busy, try again later")
    else:
        prefix = str(_backend_index(backend))

    # Stream the multipart body to the backend
    body = await request.body()
    content_type = request.headers.get("content-type", "")

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{backend}/api/render/mobile",
                content=body,
                headers={"content-type": content_type},
            )
        data = resp.json()
        # Encode composite job ID: <backend_index>_<local_job_id>
        local_id = data.get("job_id")
        return {"job_id": f"{prefix}_{local_id}"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"backend error: {exc}")


@app.get("/api/render/status/{composite_id}")
async def render_status(composite_id: str):
    """Route status check to the correct backend."""
    backend_url, local_id = _resolve_job_id(composite_id)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{backend_url}/api/render/status/{local_id}")
        return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"backend error: {exc}")


@app.get("/api/render/download/{composite_id}")
async def render_download(composite_id: str):
    """Proxy the video download from the correct backend."""
    backend_url, local_id = _resolve_job_id(composite_id)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(
                f"{backend_url}/api/render/download/{local_id}",
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="download failed")
        return Response(
            content=resp.content,
            media_type="video/mp4",
            headers={
                "content-disposition": f'attachment; filename="snapbeat_{composite_id}.mp4"'
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"backend error: {exc}")


@app.get("/api/health")
def gateway_health():
    """Aggregate health from all backends."""
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
    print(f"SnapBeat Gateway starting on port {PORT}")
    print(f"Backends: {BACKENDS}")
    if SERVERLESS_URL:
        print(f"Serverless fallback: {SERVERLESS_URL}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
