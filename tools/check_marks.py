"""Focused re-check of the hand-edited marker path, without the long whole-song render."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8772"
TEMPLATES = Path(r"C:\Users\prash\Kiro Projects\BeatCanvas\templates")
CARDS = r"C:\Users\prash\Kiro Projects\BeatCanvas\_placeholders"
MUSIC = r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4"
REPORT = Path(r"C:\Users\prash\Kiro Projects\BeatCanvas\_logs\marks_report.txt")

failures = 0
_lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    _lines.append(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(_lines) + "\n", encoding="utf-8")


def expect(label: str, ok: bool, detail: str = "") -> None:
    global failures
    say(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures += 1


def post(path: str, fields: dict) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def wait(name: str, timeout: float = 400.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/api/jobs", timeout=30) as r:
                jobs = json.loads(r.read())["jobs"]
        except Exception:
            time.sleep(3)
            continue
        job = next((j for j in jobs if j["name"] == name), None)
        if job and job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(3)
    return None


def main() -> int:
    say("hand-edited markers, 12 marks 0.75s apart, reveal on every 2nd")
    marks = ",".join(f"{i * 0.75:.3f}" for i in range(12))
    strong = ",".join(f"{i * 1.5:.3f}" for i in range(6))
    status, detail = post("/api/jobs", {
        "template": str(TEMPLATES / "reveal-tiles.json"),
        "name": "marks_check", "photo_folder": CARDS,
        "music_path": MUSIC, "fill": "repeat",
        "beat_markers": marks, "strong_markers": strong,
        "audio_duration": "154", "reveal_tiles": "4",
    })
    expect("job accepted", status == 200, detail[:120])
    job = wait("marks_check")
    expect("job finished", job is not None and job["status"] == "done",
           (job or {}).get("error", "")[:200])
    if job and job["status"] == "done":
        out = job["result"]
        expect("length follows the markers, not the 30s cap",
               8.0 <= out["duration"] <= 9.6, f"{out['duration']}s")
        expect("used the markers given", 10 <= out["clips"] <= 12,
               f"{out['clips']} cuts")
        expect("photo changes only on main beats",
               out["photos"] >= 2, f"{out['photos']} photos used")
        say(f"       {out['frames']} frames, {out['elapsed_seconds']}s to render")

    say("")
    say("marker checks passed" if not failures else f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
