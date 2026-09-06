"""Paths, external tool discovery and constants."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEMPLATE_DIR = Path(os.environ.get("BEATCANVAS_TEMPLATE_DIR", ROOT / "templates"))
OUTPUT_DIR = Path(os.environ.get("BEATCANVAS_OUTPUT_DIR", ROOT / "output"))
CACHE_DIR = ROOT / ".cache"
LOG_DIR = ROOT / "_logs"

#: Music that belongs to a fixed template lives here, so those styles keep working when
#: the original file is moved or renamed in the user's own music folder.
TEMPLATE_MUSIC_DIR = TEMPLATE_DIR / "music"

for _directory in (TEMPLATE_DIR, TEMPLATE_MUSIC_DIR, OUTPUT_DIR, CACHE_DIR, LOG_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

#: Where CapCut desktop keeps its projects and its downloaded effect bundles.
CAPCUT_ROOT = Path(os.environ.get(
    "BEATCANVAS_CAPCUT_ROOT",
    Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "User Data",
))
CAPCUT_DRAFTS = CAPCUT_ROOT / "Projects" / "com.lveditor.draft"
CAPCUT_EFFECT_CACHE = CAPCUT_ROOT / "Cache" / "effect"

_WINGET_FFMPEG = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft/WinGet/Packages"
    / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    / "ffmpeg-9.0-full_build/bin"
)


def _find_tool(name: str) -> str:
    override = os.environ.get(f"BEATCANVAS_{name.upper()}")
    if override and Path(override).exists():
        return override
    found = shutil.which(name)
    if found:
        return found
    candidate = _WINGET_FFMPEG / f"{name}.exe"
    if candidate.exists():
        return str(candidate)
    return name


FFMPEG = _find_tool("ffmpeg")
FFPROBE = _find_tool("ffprobe")

#: Frame shapes offered in the app, as (width, height).
#:
#: A template records its own canvas, but any style can be rendered to any of these:
#: positions are held in half-canvas units and horizontal travel is corrected by the
#: aspect ratio, so the motion reads the same distance on screen whatever the shape.
FRAME_SIZES: dict[str, tuple[int, int]] = {
    "portrait": (1080, 1920),
    "landscape": (1920, 1080),
    "square": (1080, 1080),
}

#: CapCut stores every time value in microseconds.
US = 1_000_000

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic"}

#: Vertical position offsets are expressed in half-canvas-heights, so 1.0 moves a layer
#: from centre to the top edge. Horizontal offsets are additionally multiplied by the
#: aspect ratio, which is what CapCut's bundles do. Confirmed against a render by
#: tools/verify.py rather than assumed.
POSITION_UNIT = "half_height"
