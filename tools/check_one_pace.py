"""Render a single pace through the app and report it.

The four-pace check renders four full videos, which takes long enough that the harness
running it tends to be reaped before the slowest one lands. This does one, so the busiest
pace can be confirmed on its own.

    tools\\run.bat tools\\check_one_pace.py accurate
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402

BASE = "http://127.0.0.1:8772"
MUSIC = r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4"
PHOTOS = config.ROOT / "_placeholders"
REPORT = config.ROOT / "_logs" / "one_pace_report.txt"

pace = sys.argv[1] if len(sys.argv) > 1 else "accurate"
seconds = sys.argv[2] if len(sys.argv) > 2 else "12"
name = f"ONE_{pace}_{time.strftime('%H%M%S')}"
lines: list[str] = []


def say(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))
    lines.append(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=60) as reply:
            return reply.status, reply.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        return 0, str(exc)


status, payload = get("/api/photos?folder=" + urllib.parse.quote(str(PHOTOS)))
if status != 200:
    say(f"cannot list photos: {status}")
    raise SystemExit(2)
pictures = [p["path"] for p in json.loads(payload)["photos"]]

body = urllib.parse.urlencode({
    "template": "auto", "name": name,
    "photo_order": "\n".join(pictures), "photo_folder": str(PHOTOS),
    "music_path": MUSIC, "fill": "repeat",
    "max_seconds": seconds, "pace": pace,
}).encode()
request = urllib.request.Request(
    BASE + "/api/jobs", data=body,
    headers={"Content-Type": "application/x-www-form-urlencoded"})
try:
    with urllib.request.urlopen(request, timeout=180) as reply:
        say(f"queued {name} at {pace} pace, {seconds}s  (HTTP {reply.status})")
except Exception as exc:
    say(f"could not queue: {exc}")
    raise SystemExit(2)

deadline = time.time() + 2400
last = ""
while time.time() < deadline:
    status, payload = get("/api/jobs")
    if status != 200:
        time.sleep(4)
        continue
    job = next((j for j in json.loads(payload)["jobs"]
                if j.get("name") == name), None)
    if job is None:
        time.sleep(4)
        continue
    stage = f"{job['status']} / {job.get('stage')}"
    if stage != last:
        say(f"  {stage}")
        last = stage
    if job["status"] == "done":
        result = job.get("result") or {}
        say(f"\n  {result.get('clips')} photo changes, "
            f"{result.get('duration')}s, {result.get('size_mb')} MB, "
            f"audio={result.get('audio')}")
        say(f"  {result.get('video')}")
        say("\n  what the director heard and decided")
        for note in result.get("notes") or []:
            if note.startswith("plan:"):
                for part in note[5:].split(" | "):
                    say(f"    {part.strip()[:150]}")
            else:
                say(f"    {note[:150]}")
        raise SystemExit(0)
    if job["status"] in ("failed", "cancelled"):
        say(f"\n  {job['status']}: {str(job.get('error'))[:400]}")
        raise SystemExit(1)
    time.sleep(4)

say("timed out waiting for the render")
raise SystemExit(1)
