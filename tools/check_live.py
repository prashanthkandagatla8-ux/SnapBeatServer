"""Live checks for the new endpoints and the whole-song fix, end to end."""
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
REPORT = Path(r"C:\Users\prash\Kiro Projects\BeatCanvas\_logs\live_report.txt")

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


def get(path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=180) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


def post(path: str, fields: dict) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def wait(name: str, timeout: float = 900.0) -> dict | None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        status, body = get("/api/jobs")
        if status == 200:
            job = next((j for j in json.loads(body)["jobs"]
                        if j["name"] == name), None)
            if job:
                if job["status"] != last:
                    say(f"       {job['status']:12s} {job['stage']}")
                    last = job["status"]
                if job["status"] in ("done", "failed", "cancelled"):
                    return job
        time.sleep(3)
    return None


def main() -> int:
    status, body = get("/")
    page = body.decode("utf-8", "replace")
    expect("page renders", status == 200 and "Find the beats" in page,
           f"status {status}")
    for marker in ("Reveal Tiles", "Drop photos and music here",
                   "Pieces per photo", "All main", "Delete small beats"):
        expect(f"page has '{marker}'", marker in page)

    say("\nbeat analysis endpoint")
    status, body = get("/api/analyse?music=" + urllib.parse.quote(MUSIC) +
                       "&seconds=20")
    expect("analyse responds", status == 200, f"status {status}")
    if status == 200:
        d = json.loads(body)
        expect("returns beats", len(d["beats"]) > 10, f"{len(d['beats'])}")
        expect("labels main and small",
               0 < d["strong_count"] < len(d["beats"]),
               f"{d['strong_count']} main of {len(d['beats'])}")
        expect("returns an envelope to draw", len(d["envelope"]) > 100,
               f"{len(d['envelope'])} points")
        expect("honours the requested window", d["span"] <= 20.5,
               f"span {d['span']}s of {d['duration']}s")
        say(f"       {d['tempo_bpm']} BPM")

    status, _ = get("/api/analyse?music=" + urllib.parse.quote(CARDS))
    expect("analyse rejects a non-audio path", status == 400, f"status {status}")

    say("\nwhole song, end to end")
    status, _ = post("/api/jobs", {
        "template": str(TEMPLATES / "beat-cut.json"),
        "name": "live_whole", "photo_folder": CARDS,
        "music_path": MUSIC, "fill": "repeat",
        "max_seconds": "0", "beats_per_clip": "4",
    })
    expect("whole-song job accepted", status == 200, f"status {status}")
    job = wait("live_whole")
    expect("whole-song job finished", job is not None and job["status"] == "done",
           (job or {}).get("error", "")[:200])
    if job and job["status"] == "done":
        out = job["result"]
        expect("length follows the song, not the old 30s cap",
               out["duration"] > 100.0,
               f"{out['duration']}s from a {154}s track")
        say(f"       {out['clips']} cuts, {out['frames']} frames, "
            f"{out['elapsed_seconds']}s to render, {out['size_mb']}MB")

    say("\nhand-edited markers, end to end")
    marks = ",".join(f"{i * 0.75:.3f}" for i in range(12))
    strong = ",".join(f"{i * 1.5:.3f}" for i in range(6))
    status, _ = post("/api/jobs", {
        "template": str(TEMPLATES / "reveal-tiles.json"),
        "name": "live_marks", "photo_folder": CARDS,
        "music_path": MUSIC, "fill": "repeat",
        "beat_markers": marks, "strong_markers": strong,
        "audio_duration": "154", "reveal_tiles": "4",
    })
    expect("edited-marker job accepted", status == 200, f"status {status}")
    job = wait("live_marks")
    expect("edited-marker job finished",
           job is not None and job["status"] == "done",
           (job or {}).get("error", "")[:200])
    if job and job["status"] == "done":
        out = job["result"]
        expect("used the markers given", 8 <= out["clips"] <= 12,
               f"{out['clips']} cuts")
        expect("length matches the markers", 8.0 <= out["duration"] <= 9.5,
               f"{out['duration']}s")

    status, detail = post("/api/jobs", {
        "template": str(TEMPLATES / "beat-cut.json"),
        "name": "live_bad", "photo_folder": CARDS, "music_path": MUSIC,
        "beat_markers": "1.0",
    })
    expect("a single marker is rejected", status == 400, detail[:80])

    say("")
    say("live checks passed" if not failures else f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
