"""Local single-page UI.

Loopback only and unauthenticated: a single-user tool on the operator's own machine,
where a login would be friction with no attacker to stop. That holds only while it stays
on 127.0.0.1, because the endpoints below deliberately read the user's own folders.

Everything lives on one page. There is no preview stage and no per-job page; a render
finishes in well under a minute, and the result is revealed in Explorer.
"""
from __future__ import annotations

import io
import logging
import os
import subprocess
import sys

logger = logging.getLogger("beatcanvas.app")
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
)
from fastapi.templating import Jinja2Templates

from . import (analysis, animations, beats, choreography, config, dialogs, effects,
               photos as photo_mod, store)
from .template import (FILL_MODES, MIN_PHOTOS, MUSIC_MODES, REVEAL_SHAPES, Template,
                       list_templates)
from .worker import AUTO_TEMPLATE, DIRECTIONS, Worker

TEMPLATE_DIR = Path(__file__).parent / "web" / "templates"

job_store = store.JobStore()
worker = Worker(job_store)

@asynccontextmanager
async def lifespan(app: FastAPI):
    worker.start()
    yield
    worker.stop()

app = FastAPI(title="BeatCanvas", docs_url=None, redoc_url=None, lifespan=lifespan)
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8772", "http://localhost:8772", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
views = Jinja2Templates(directory=str(TEMPLATE_DIR))

AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".mp4", ".mov"}


def _template_cards() -> list[dict]:
    cards = []
    for path in list_templates():
        try:
            template = Template.load(path)
        except Exception:
            continue
        cards.append({
            "path": str(path),
            "name": template.name,
            "music_mode": template.music_mode,
            "animation": template.animation if template.music_mode == "any"
            else (sorted({c.animation for c in template.clips if c.animation})
                  or ["Cut"])[0],
            "clips": len(template.clips),
            "slots": template.slot_count,
            "duration": round(template.duration, 1),
            "size": f"{template.width}x{template.height}",
            "audio": template.audio_name or Path(template.audio_path).name,
            "cover_mode": template.cover_mode if template.music_mode == "any"
            else (template.clips[0].cover_mode if template.clips else "zoom"),
            "reveal_tiles": template.reveal_tiles,
            "reveal_shape": template.reveal_shape,
            "beats_per_clip": template.beats_per_clip,
        })
    # Music-agnostic templates first: they are the ones usable with anything.
    return sorted(cards, key=lambda c: (c["music_mode"] != "any", c["name"]))


# ------------------------------------------------------------------------ page


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return views.TemplateResponse(request, "index.html", {
        "request": request,
        "templates": _template_cards(),
        "jobs": job_store.list(25),
        "animations": animations.SELECTABLE,
        "cover_modes": animations.COVER_MODES,
        "directions": ["up", "down", "left", "right"],
        "fill_modes": FILL_MODES,
        # A separate axis from the reading: what the animation is, rather than when it
        # happens. Each look lists the arrivals it permits so the choice is concrete.
        "looks": [
            {"name": item.name, "label": item.label, "note": item.note,
             "uses": ", ".join(item.reveals) or "everything"}
            for item in effects.LOOKS.values()
        ],
        # Several readings of the same track rather than one verdict, because tempo and
        # metre are estimates and a wrong one is quicker to step around than to argue with.
        "analysis_presets": [
            {"name": name, "label": name.replace("-", " ").title(), "note": item.note}
            for name, item in analysis.PRESETS.items()
        ],
        "paces": [
            {"name": name,
             "label": f"{name.capitalize()} - " + (
                 "one photo every few bars" if name == "slow" else
                 "a photo every couple of bars" if name == "medium" else
                 "a photo per bar" if name == "fast" else
                 "twice a bar, reacting to everything"),
             "note": pace.note}
            for name, pace in choreography.PACES.items()
        ],
        "default_output": str(config.OUTPUT_DIR),
        "min_photos": MIN_PHOTOS,
        "placeholder_dir": str(config.ROOT / "_placeholders"),
    })


# ------------------------------------------------------------------- browsing


@app.get("/api/photos")
def list_photos(folder: str):
    """What a folder contains, in the order the renderer would use it."""
    target = Path(folder.strip().strip('"'))
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"not a folder: {target}")
    found = photo_mod.collect(target)
    if not found:
        raise HTTPException(status_code=400,
                            detail=f"no images found in {target.name}")
    return {
        "folder": str(target),
        "count": len(found),
        "photos": [{"name": p.name, "path": str(p)} for p in found],
    }


@app.get("/api/music")
def list_music(folder: str):
    """Audio files in a folder, so a track can be picked without typing a path."""
    target = Path(folder.strip().strip('"'))
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"not a folder: {target}")
    found = sorted(
        p for p in target.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
    )
    if not found:
        raise HTTPException(status_code=400,
                            detail=f"no audio files found in {target.name}")
    return {"folder": str(target),
            "tracks": [{"name": p.name, "path": str(p)} for p in found]}


@app.get("/api/analyse")
def analyse_music(music: str, seconds: float = 0.0, sensitivity: float = 1.0,
                  strong_ratio: float = 0.4):
    """Detect beats and return everything the editor needs to draw and adjust them.

    The onset envelope goes back too, downsampled for plotting, so the markers can be
    seen against the sound they came from rather than floating on an empty strip.
    """
    track = Path(music.strip().strip('"'))
    if not track.is_file():
        raise HTTPException(status_code=400, detail=f"not a file: {track}")
    if track.suffix.lower() not in AUDIO_SUFFIXES:
        raise HTTPException(status_code=400,
                            detail=f"{track.suffix} is not an audio format I read")

    window = float(seconds) if seconds and seconds > 0 else None
    try:
        analysis = beats.analyse(
            track,
            duration=(window + 6.0) if window else None,
            sensitivity=max(0.2, min(4.0, float(sensitivity))),
            strong_ratio=max(0.05, min(0.95, float(strong_ratio))),
        )
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    span = window or analysis.duration
    return {
        "track": track.name,
        "duration": round(analysis.duration, 3),
        "span": round(min(span, analysis.duration), 3),
        "tempo_bpm": round(analysis.tempo_bpm, 1),
        "beats": [b.to_dict() for b in analysis.beats
                  if b.time <= min(span, analysis.duration) + 0.01],
        "strong_count": sum(1 for b in analysis.beats if b.strong),
        "envelope": analysis.envelope_plot(700),
    }


@app.get("/thumb")
def thumbnail(path: str, size: int = 200):
    """A small preview of one photo, for the reordering grid.

    Only image files are served, and only ever downscaled, so the original is never
    handed out at full resolution just to draw a thumbnail.
    """
    target = Path(path)
    try:
        resolved = target.resolve(strict=True)
    except OSError:
        raise HTTPException(status_code=404, detail="not found")
    if resolved.suffix.lower() not in config.IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="not an image")

    image = cv2.imread(str(resolved), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="could not read that image")

    side = max(48, min(400, int(size)))
    height, width = image.shape[:2]
    scale = side / max(width, height)
    if scale < 1.0:
        image = cv2.resize(image, (max(1, int(width * scale)),
                                   max(1, int(height * scale))),
                           interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 78])
    if not ok:
        raise HTTPException(status_code=500, detail="could not encode thumbnail")
    return Response(content=buffer.tobytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "max-age=3600"})


# ----------------------------------------------------------------------- jobs


@app.post("/api/jobs")
def create_job(
    template: str = Form(...),
    name: str = Form(""),
    photo_order: str = Form(""),
    photo_folder: str = Form(""),
    music_path: str = Form(""),
    fill: str = Form("repeat"),
    direction: str = Form(""),
    intensity: str = Form(""),
    cover_mode: str = Form(""),
    frame: str = Form(""),
    reveal_shape: str = Form(""),
    pace: str = Form(""),
    # Aliased because the plain name would shadow the analysis module in this scope.
    reading: str = Form("auto", alias="analysis"),
    look: str = Form("mix"),
    beats_per_clip: str = Form(""),
    max_seconds: str = Form(""),
    reveal_tiles: str = Form(""),
    beat_markers: str = Form(""),
    strong_markers: str = Form(""),
    audio_duration: str = Form(""),
    output_dir: str = Form(""),
):
    # The automatic mode designs the edit from the track, so there is no template to load
    # and none of a template's settings apply.
    auto = template.strip() == AUTO_TEMPLATE
    loaded: Template | None = None
    if not auto:
        template_path = Path(template)
        if not template_path.exists():
            raise HTTPException(status_code=400, detail="unknown template")
        loaded = Template.load(template_path)

    ordered = [line.strip() for line in photo_order.splitlines() if line.strip()]
    if not ordered and not photo_folder.strip():
        raise HTTPException(status_code=400, detail="choose a photo folder first")
    if ordered and len(ordered) < MIN_PHOTOS:
        raise HTTPException(status_code=400,
                            detail=f"keep at least {MIN_PHOTOS} photos")

    if not music_path.strip():
        if auto:
            raise HTTPException(
                status_code=400,
                detail="the automatic mode designs the edit from the music, "
                       "so choose a track")
        if loaded is not None and loaded.music_mode == "any":
            raise HTTPException(
                status_code=400,
                detail=f"'{loaded.name}' works with any music, so choose a music file")

    style_name = "Auto" if auto else loaded.name          # type: ignore[union-attr]
    job_name = (name.strip() or style_name).replace(" ", "_").replace("+", "and")
    options = {
        "photo_folder": photo_folder.strip().strip('"'),
        "photo_order": ordered,
        "music_path": music_path.strip().strip('"'),
        "fill": fill if fill in FILL_MODES else "repeat",
        "output_dir": output_dir.strip() or str(config.OUTPUT_DIR),
    }
    # Only record overrides that were actually set, so a template's own settings survive.
    if direction in DIRECTIONS:
        options["direction"] = direction
    if intensity.strip():
        options["intensity"] = max(0.0, min(2.0, float(intensity)))
    if cover_mode in animations.COVER_MODES:
        options["cover_mode"] = cover_mode
    if beats_per_clip.strip():
        options["beats_per_clip"] = max(1, int(beats_per_clip))
    if max_seconds.strip():
        # Recorded even when zero: zero means "the whole song", which is a choice.
        options["max_seconds"] = max(0.0, float(max_seconds))
    if frame in config.FRAME_SIZES:
        options["frame"] = frame
    # Only a style that reveals in pieces may have its piece settings changed. Accepting
    # these for any style would quietly turn every template into a reveal, which is
    # precisely the bug this guards against.
    if loaded is not None and loaded.reveal_tiles > 0:
        # The number of pieces follows the music, so only the outline is a choice here.
        if reveal_shape in REVEAL_SHAPES:
            options["reveal_shape"] = reveal_shape
        if reveal_tiles.strip():
            options["reveal_tiles"] = max(2, int(reveal_tiles))
    if auto:
        options["pace"] = pace if pace in choreography.PACES else "medium"
        options["analysis"] = reading if reading in analysis.PRESETS else "auto"
        options["look"] = look if look in effects.LOOKS else "mix"

    if beat_markers.strip():
        marks = sorted({round(float(v), 4) for v in beat_markers.split(",")
                        if v.strip()})
        if len(marks) < 2:
            raise HTTPException(status_code=400,
                                detail="keep at least 2 beat markers")
        options["beat_markers"] = marks
        if strong_markers.strip():
            options["strong_markers"] = sorted(
                {round(float(v), 4) for v in strong_markers.split(",") if v.strip()})
        if audio_duration.strip():
            options["audio_duration"] = max(0.0, float(audio_duration))

    # The automatic mode records the sentinel rather than a path, which is what tells the
    # worker to design the edit instead of loading a style.
    job = job_store.create(job_name,
                           AUTO_TEMPLATE if auto else str(template_path), options)
    worker.notify()
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: int):
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    worker.request_cancel(job_id)
    if job.status == store.STATUS_QUEUED:
        job_store.update(job_id, status=store.STATUS_CANCELLED, stage="cancelled")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/delete")
def delete_job(job_id: int):
    job_store.delete(job_id)
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": [job.to_dict() for job in job_store.list(25)]}


@app.post("/api/jobs/clear")
def clear_jobs():
    """Remove all finished, failed and cancelled jobs at once."""
    removed = 0
    for job in job_store.list(200):
        if job.status not in store.ACTIVE_STATUSES:
            job_store.delete(job.id)
            removed += 1
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/drop")
async def receive_drop(files: list[UploadFile] = File(...)):
    """Take files dragged onto the page and copy them into a working folder.

    A browser will not reveal where a dropped file came from, so it has to be copied
    somewhere the renderer can reach. Names are reduced to safe characters and only the
    base name is kept, so a dropped file cannot write outside the folder below.
    """
    inbox = config.ROOT / "_dropped"
    photos_dir = inbox / "photos"
    music_dir = inbox / "music"
    photos_dir.mkdir(parents=True, exist_ok=True)
    music_dir.mkdir(parents=True, exist_ok=True)

    saved_photos: list[str] = []
    saved_music: list[str] = []
    skipped: list[str] = []

    for upload in files:
        if not upload or not upload.filename:
            continue
        name = Path(upload.filename.replace("\\", "/")).name
        clean = "".join(c if (c.isalnum() or c in "-_. ") else "_" for c in name).strip()
        suffix = Path(clean).suffix.lower()

        if suffix in config.IMAGE_EXTENSIONS:
            target = photos_dir / clean
            bucket = saved_photos
        elif suffix in AUDIO_SUFFIXES:
            target = music_dir / clean
            bucket = saved_music
        else:
            skipped.append(name)
            continue

        data = await upload.read()
        if not data:
            skipped.append(name)
            continue
        target.write_bytes(data)
        bucket.append(str(target))

    return {
        "photo_folder": str(photos_dir) if saved_photos else "",
        "photos": len(saved_photos),
        "music": saved_music[-1] if saved_music else "",
        "music_count": len(saved_music),
        "skipped": skipped,
    }


@app.post("/api/clear-drops")
def clear_drops():
    """Empty the drop folder, so a new set of photos does not mix with the last."""
    import shutil

    inbox = config.ROOT / "_dropped"
    shutil.rmtree(inbox, ignore_errors=True)
    return {"ok": True}


@app.post("/api/browse")
def browse(request: Request, kind: str = Form("folder"), start: str = Form("")):
    client_ip = request.client.host if request and request.client else ""
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Endpoint restricted to localhost")

    """Show the operating system's own file or folder picker.

    Only worth having because this runs on your machine: a page served from the internet
    could not do it. Cancelling is an ordinary outcome, so it comes back as a plain answer
    rather than an error the page has to catch.
    """
    if kind not in dialogs.KINDS:
        raise HTTPException(status_code=400, detail=f"unknown dialog: {kind}")
    try:
        chosen = dialogs.pick(kind, start.strip().strip('"'))
    except dialogs.Cancelled:
        return {"cancelled": True}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408,
                            detail="the dialog was left open too long")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:200])

    if kind == "photos":
        paths = [line for line in chosen.splitlines() if line.strip()]
        return {"paths": paths, "folder": str(Path(paths[0]).parent) if paths else ""}
    return {"path": chosen}


@app.post("/api/open-folder")
def open_folder(request: Request, path: str = Form("")):
    client_ip = request.client.host if request and request.client else ""
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Endpoint restricted to localhost")

    """Open a folder in Explorer so finished videos can be played.

    Guarded to folders belonging to this project or ones the app was told to write to,
    for the same reason the reveal endpoint is: an unguarded "open this path" is a way to
    poke around the machine from a web page.
    """
    target = Path(path.strip().strip('"') or config.OUTPUT_DIR)
    if target.is_file():
        target = target.parent
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="that folder does not exist")

    allowed = [config.ROOT.resolve(), config.OUTPUT_DIR.resolve()]
    resolved = target.resolve()
    if not any(resolved == root or root in resolved.parents for root in allowed):
        raise HTTPException(status_code=403,
                            detail="only this app's own folders can be opened")
    subprocess.Popen(["explorer", str(resolved)])
    return {"opened": str(resolved)}


@app.post("/api/reveal")
def reveal(request: Request, path: str = Form(...)):
    client_ip = request.client.host if request and request.client else ""
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Endpoint restricted to localhost")

    """Open the containing folder with the file selected.

    A local convenience, so the operator does not have to go hunting for the output.
    The path is checked to be something BeatCanvas actually produced before any process
    is started, and the file name is passed as an argument rather than through a shell,
    so a crafted name cannot turn into a command.
    """
    target = Path(path)
    try:
        resolved = target.resolve(strict=True)
    except OSError:
        raise HTTPException(status_code=404, detail="that file is no longer there")

    known_roots = [config.OUTPUT_DIR.resolve(), config.ROOT.resolve()]
    if not any(root == resolved or root in resolved.parents for root in known_roots):
        raise HTTPException(status_code=403,
                            detail="only BeatCanvas's own output can be revealed")

    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{resolved}"], shell=False)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(resolved)], shell=False)
    else:
        subprocess.Popen(["xdg-open", str(resolved.parent)], shell=False)
    return {"ok": True}


@app.get("/api/health")
def health():
    return JSONResponse({
        "ok": True,
        "ffmpeg": config.FFMPEG,
        "templates": len(list_templates()),
        "animations": list(animations.SELECTABLE),
        "music_modes": list(MUSIC_MODES),
        "worker_job": worker.current_job_id,
    })

import asyncio
from fastapi.responses import FileResponse
from typing import List

def sanitize_filename(name: str) -> str:
    clean = "".join(c if (c.isalnum() or c in "-_. ") else "_" for c in name).strip()
    return clean or "unnamed_file"

@app.post("/api/render/mobile")
def render_mobile(
    request: Request,
    audio: UploadFile = File(...),
    photos: List[UploadFile] = File(...),
    template: str = Form(None),
    drop_it: str = Form(None),
    audio_start: str = Form("0"),
    audio_end: str = Form("0"),
    full_track: str = Form("true"),
    frame: str = Form("portrait"),
    title_text: str = Form(""),
    title_bg: str = Form("black"),
    title_duration: str = Form("2"),
    title_font: str = Form("impact"),
    title_style: str = Form("classic"),
    title_frame: str = Form("none"),
    quality: str = Form("fast"),
    auto_arrange: str = Form("auto"),
    watermark: str = Form("true"),
    render_type: str = Form("free_queue"),
    cover_mode: str = Form(None)
):
    # Security check: If SNAPBEAT_INTERNAL_SECRET is configured, require authorization header
    internal_secret = os.environ.get("SNAPBEAT_INTERNAL_SECRET", "").strip()
    if internal_secret:
        client_auth = request.headers.get("X-Serverless-Auth", "").strip()
        if client_auth != internal_secret:
            raise HTTPException(status_code=401, detail="Unauthorized: Serverless render requires verified access.")

    import shutil
    import uuid

    job_id_str = str(uuid.uuid4())
    inbox = config.ROOT / "_dropped" / job_id_str
    photos_dir = inbox / "photos"
    music_dir = inbox / "music"
    photos_dir.mkdir(parents=True, exist_ok=True)
    music_dir.mkdir(parents=True, exist_ok=True)

    audio_clean = sanitize_filename(audio.filename or "audio.ext")
    audio_path = music_dir / audio_clean
    with open(audio_path, "wb") as f:
        shutil.copyfileobj(audio.file, f)

    photo_paths = []
    for i, p in enumerate(photos):
        p_clean = sanitize_filename(p.filename or "photo.ext")
        # Fix: Prefix with index to prevent overwrite when filenames collide
        p_path = photos_dir / f"{i}_{p_clean}"
        with open(p_path, "wb") as f:
            shutil.copyfileobj(p.file, f)
        photo_paths.append(str(p_path.resolve()))

    # Smart Photo Arrangement (Gemini Multimodal AI + Computer Vision Fallback)
    arranged_photos = photo_paths
    if (auto_arrange or "auto").lower() in ("auto", "true", "1", "yes"):
        try:
            from . import smart_arranger
            arranged_photos = smart_arranger.auto_arrange_photos(
                photo_paths,
                audio_path=str(audio_path.resolve())
            )
        except Exception as e:
            logger.warning(f"Auto-arrange execution error: {e}")
            arranged_photos = photo_paths

    start_sec = max(0.0, float(audio_start or 0))
    end_sec = max(0.0, float(audio_end or 0))
    is_full = full_track.lower() in ("true", "1", "yes") or end_sec <= 0 or end_sec <= start_sec
    max_sec = 0.0 if is_full else max(5.0, end_sec - start_sec)

    options = {
        "photo_folder": str(photos_dir.resolve()),
        "photo_order": arranged_photos,
        "auto_arrange": (auto_arrange or "auto").lower() in ("auto", "true", "1", "yes"),
        "music_path": str(audio_path.resolve()),
        "fill": "repeat",
        "analysis": "auto",
        "look": "mix",
        "frame": frame if frame in ("portrait", "landscape", "square") else "portrait",
        "pace": "medium",
        "intensity": 1.0,
        "cover_mode": cover_mode.lower().strip() if (cover_mode and cover_mode.lower().strip() in ("zoom", "mirror", "fit", "contain")) else None,
        "reveal_shape": "circle",
        "drop_it": drop_it in ("true", "1", "yes"),
        "audio_start": start_sec,
        "audio_end": end_sec,
        "max_seconds": max_sec,
        "title_text": (title_text or "").strip(),
        "title_bg": title_bg if title_bg in ("black", "video") or title_bg.startswith("#") else "black",
        "title_duration": max(1, min(5, int(title_duration or 2))),
        "title_font": (title_font or "impact").lower().strip(),
        "title_style": (title_style or "classic").lower().strip(),
        "title_frame": (title_frame or "none").lower().strip(),
        "quality": (quality or "fast").lower().strip(),
        "watermark": (watermark or "true").lower() in ("true", "1", "yes"),
        "render_type": (render_type or "free_queue").lower().strip(),
    }

    # Fix: Sanitize template name — strip .json suffix, block path traversal
    if template:
        template = template.removesuffix(".json")
        template = Path(template).name  # strip any directory traversal
    chosen_template = str(config.TEMPLATE_DIR / f"{template}.json") if template else str(config.TEMPLATE_DIR / "simple.json")
    job = job_store.create(f"mobile_{job_id_str[:8]}", chosen_template, options)
    worker.notify()
    return {"job_id": job.id}

@app.get("/api/render/status/{job_id}")
def render_status(job_id: int):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    
    display_stage = job.stage
    if job.status == "queued":
        display_stage = "Waiting in queue..."
        
    return {
        "status": job.status,
        "stage": display_stage,
        "progress": round((job.progress or 0.0) * 100),
        "error": job.error
    }

@app.get("/api/render/download/{job_id}")
def render_download(job_id: int, delete_after: bool = False, background_tasks: BackgroundTasks = BackgroundTasks()):
    import shutil
    job = job_store.get(job_id)
    if not job or job.status != store.STATUS_DONE:
        raise HTTPException(status_code=400, detail="job not done")
    video_file = Path(job.result.get("video"))
    if not video_file.exists():
        raise HTTPException(status_code=404, detail="video file no longer on server")

    if delete_after:
        def _deferred_cleanup():
            try:
                # Clean up rendered video
                video_file.unlink(missing_ok=True)
                # Clean up uploaded raw input folder
                if job.name and job.name.startswith("mobile_"):
                    dropped_dir = config.ROOT / "_dropped" / job.name.replace("mobile_", "")
                    if dropped_dir.exists():
                        shutil.rmtree(dropped_dir, ignore_errors=True)
            except Exception:
                pass
        background_tasks.add_task(_deferred_cleanup)

    return FileResponse(str(video_file), media_type="video/mp4", filename=f"snapbeat_{job_id}.mp4")


@app.post("/api/render/cleanup/{job_id}")
def render_cleanup(job_id: int):
    """Explicitly delete a completed or cancelled render and its inputs from server."""
    import shutil
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    
    cleaned = []
    if job.result and job.result.get("video"):
        p = Path(job.result.get("video"))
        if p.exists():
            p.unlink(missing_ok=True)
            cleaned.append("video")
            
    if job.name and job.name.startswith("mobile_"):
        dropped_dir = config.ROOT / "_dropped" / job.name.replace("mobile_", "")
        if dropped_dir.exists():
            shutil.rmtree(dropped_dir, ignore_errors=True)
            cleaned.append("inputs")
            
    return {"status": "ok", "cleaned": cleaned}


# -- Health endpoint (used by gateway for load balancing) --------------------

@app.get("/api/health")
def api_health():
    """Report server health and current load for gateway routing."""
    import time
    active = job_store.count_active()
    return {
        "status": "ok",
        "active_jobs": active,
        "max_workers": config.MAX_WORKERS,
        "available": max(0, config.MAX_WORKERS - active),
        "timestamp": time.time(),
    }


# -- Auto-cleanup of old rendered videos ------------------------------------

import threading
import time as _time

def _cleanup_old_outputs():
    """Delete rendered MP4s older than OUTPUT_MAX_AGE_SECONDS to prevent disk fill."""
    while True:
        _time.sleep(1800)  # run every 30 minutes
        try:
            cutoff = _time.time() - config.OUTPUT_MAX_AGE_SECONDS
            for f in config.OUTPUT_DIR.glob("*.mp4"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
        except Exception:
            pass

_cleanup_thread = threading.Thread(target=_cleanup_old_outputs, daemon=True)
_cleanup_thread.start()

