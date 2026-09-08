"""SnapBeat Smart Photo Arranger.

Analyzes photos and audio beat structure to sequence photos for maximum musical impact.
- Uses Google Gemini Multimodal AI (free tier) if an API key is available.
- Falls back to an on-server Computer Vision energy analyzer (PIL/OpenCV) if offline or without a key.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from PIL import Image, ImageFilter, ImageStat
import numpy as np

from . import beats

logger = logging.getLogger("beatcanvas.smart_arranger")


def _get_gemini_api_key() -> Optional[str]:
    """Retrieve Gemini API key from environment or config file."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    try:
        cfg_path = Path(__file__).parent.parent / "config.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("gemini_api_key", "").strip() or None
    except Exception:
        pass
    return None


def _make_thumbnail_b64(path: str, max_size: int = 256) -> Optional[str]:
    """Load image, downscale to thumbnail, return base64 JPEG."""
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        logger.warning(f"Failed to thumbnail {path}: {e}")
        return None


def _score_image_heuristic(path: str) -> float:
    """Compute visual energy score for an image using PIL (0.0 - 1.0)."""
    try:
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            rgb.thumbnail((200, 200), Image.Resampling.BILINEAR)

            # 1. Saturation
            hsv = rgb.convert("HSV")
            _, s, _ = hsv.split()
            stat_s = ImageStat.Stat(s)
            sat_score = (stat_s.mean[0] / 255.0) * 0.5 + (stat_s.stddev[0] / 128.0) * 0.5
            sat_score = min(1.0, max(0.0, sat_score))

            # 2. Contrast
            gray = rgb.convert("L")
            stat_gray = ImageStat.Stat(gray)
            contrast_score = min(1.0, stat_gray.stddev[0] / 64.0)

            # 3. Sharpness / Detail (edge energy)
            edges = gray.filter(ImageFilter.FIND_EDGES)
            stat_edges = ImageStat.Stat(edges)
            sharp_score = min(1.0, stat_edges.mean[0] / 30.0)

            total_score = 0.40 * sat_score + 0.35 * contrast_score + 0.25 * sharp_score
            return round(total_score, 4)
    except Exception:
        return 0.5


def _arrange_heuristic(photo_paths: list[str], climax_ratio: float = 0.55) -> list[str]:
    """Arrange photos along a dramatic narrative energy curve."""
    n = len(photo_paths)
    if n <= 2:
        return list(photo_paths)

    scores = [(path, _score_image_heuristic(path)) for path in photo_paths]
    sorted_by_score = sorted(scores, key=lambda x: x[1], reverse=True)

    climax_idx = int(round(climax_ratio * (n - 1)))
    climax_idx = max(0, min(n - 1, climax_idx))

    slot_distances = [(i, abs(i - climax_idx)) for i in range(n)]
    slot_distances.sort(key=lambda x: x[1])

    result = [None] * n
    for rank, (slot_idx, _) in enumerate(slot_distances):
        result[slot_idx] = sorted_by_score[rank][0]

    return [p for p in result if p is not None]


def _call_gemini_multimodal(
    photo_paths: list[str],
    tempo_bpm: float,
    climax_sec: float,
    api_key: str
) -> Optional[list[int]]:
    """Call Google Gemini Flash API to sequence photos."""
    n = len(photo_paths)
    thumbnails = []
    for p in photo_paths:
        b64 = _make_thumbnail_b64(p)
        if not b64:
            return None
        thumbnails.append(b64)

    prompt_text = (
        f"You are a music video director. Analyze these {n} numbered photos (indices 0 to {n-1}).\n"
        f"The track tempo is ~{int(tempo_bpm)} BPM with a major bass drop / climax hit around {climax_sec:.1f}s.\n"
        "Sequence these photos into a compelling narrative arc:\n"
        "- Establishing/scenery/ambient photos at the start (intro)\n"
        "- Building anticipation\n"
        "- Place the single most vibrant, emotional face, or dramatic action shot directly at the climax\n"
        "- Celebration/action shots following through\n"
        f"Return ONLY a JSON array of integers of all {n} indices [0..{n-1}] in your recommended order.\n"
        "Example: [3, 0, 5, 1, 4, 2]"
    )

    parts = [{"text": prompt_text}]
    for i, b64 in enumerate(thumbnails):
        parts.append({"text": f"Photo index {i}:"})
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": b64
            }
        })

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 300
        }
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            if response.status != 200:
                return None
            res_data = json.loads(response.read().decode("utf-8"))
            candidates = res_data.get("candidates", [])
            if not candidates:
                return None
            text_resp = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            match = re.search(r"\[[\d\s,]+\]", text_resp)
            if match:
                indices = json.loads(match.group(0))
                if isinstance(indices, list) and len(indices) == n and set(indices) == set(range(n)):
                    return indices
    except Exception as e:
        logger.warning(f"Gemini API call failed: {e}")
        return None

    return None


def auto_arrange_photos(
    photo_paths: list[str],
    audio_path: Optional[str] = None,
    gemini_key: Optional[str] = None
) -> list[str]:
    """Main entrypoint: reorder photo_paths for optimal musical impact."""
    if not photo_paths or len(photo_paths) <= 2:
        return list(photo_paths)

    tempo_bpm = 120.0
    climax_ratio = 0.55
    climax_sec = 10.0
    if audio_path and Path(audio_path).exists():
        try:
            analysis = beats.analyse(audio_path, duration=30.0)
            if analysis.tempo_bpm > 30:
                tempo_bpm = analysis.tempo_bpm
            if analysis.duration > 2 and analysis.strengths:
                peak_idx = int(np.argmax(analysis.strengths))
                climax_sec = float(analysis.onsets[peak_idx])
                climax_ratio = min(0.85, max(0.25, climax_sec / max(1.0, analysis.duration)))
        except Exception as e:
            logger.info(f"Audio beat analysis for auto-arrange skipped: {e}")

    api_key = gemini_key or _get_gemini_api_key()
    if api_key:
        try:
            indices = _call_gemini_multimodal(photo_paths, tempo_bpm, climax_sec, api_key)
            if indices:
                logger.info(f"Auto-arranged {len(photo_paths)} photos via Gemini Flash: {indices}")
                return [photo_paths[i] for i in indices]
        except Exception as e:
            logger.warning(f"Gemini auto-arrange fallback triggered: {e}")

    arranged = _arrange_heuristic(photo_paths, climax_ratio)
    logger.info(f"Auto-arranged {len(photo_paths)} photos via Local CV Heuristic engine (climax at {climax_ratio:.2f})")
    return arranged
