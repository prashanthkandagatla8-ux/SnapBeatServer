"""Drive the single page end to end against the running server.

Covers the paths the smoke test cannot: the page renders, folders can be listed,
thumbnails are produced, a job submitted through the form actually renders, and the
music-agnostic path works with a track the template has never seen.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8772"
CARDS = r"C:\Users\prash\Kiro Projects\BeatCanvas\_placeholders"
MUSIC = r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4"

failures = 0

# Shell redirection has proved unreliable in this environment, so the report is written
# from inside the process rather than piped by the caller.
REPORT = Path(__file__).resolve().parent.parent / "_logs" / "ui_report.txt"
_lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    _lines.append(text)
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def expect(label: str, ok: bool, detail: str = "") -> None:
    global failures
    say(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures += 1


def get(path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:
        return 0, str(exc).encode()


def post(path: str, fields: dict) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(BASE + path, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, response.geturl()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def wait_done(name: str, timeout: float = 300.0) -> dict | None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        status, body = get("/api/jobs")
        if status != 200:
            time.sleep(2)
            continue
        jobs = json.loads(body)["jobs"]
        job = next((j for j in jobs if j["name"] == name), None)
        if job:
            if job["status"] != last:
                print(f"       {job['status']:12s} {job['stage']}")
                last = job["status"]
            if job["status"] in ("done", "failed", "cancelled"):
                return job
        time.sleep(2)
    return None


def main() -> int:
    status, body = get("/api/health")
    expect("health responds", status == 200)
    if status == 200:
        data = json.loads(body)
        print(f"       {data['templates']} templates, "
              f"animations={', '.join(data['animations'])}")

    status, body = get("/")
    page = body.decode("utf-8", "replace")
    expect("single page renders", status == 200 and "Pick a style" in page,
           f"status {status}, {len(page)} bytes")
    for marker in ("Photos", "Music", "Options", "Your videos",
                   "Make the video", "any music", "its own song"):
        expect(f"page shows '{marker}'", marker in page)
    for gone in ("Import a CapCut draft", "Retime", "contact sheet"):
        expect(f"page no longer shows '{gone}'", gone not in page)

    status, body = get("/api/photos?folder=" + urllib.parse.quote(CARDS))
    expect("lists a photo folder", status == 200)
    photos = json.loads(body)["photos"] if status == 200 else []
    print(f"       {len(photos)} photos")

    if photos:
        status, body = get("/thumb?path=" + urllib.parse.quote(photos[0]["path"]))
        expect("serves a thumbnail", status == 200 and body[:3] == b"\xff\xd8\xff",
               f"{len(body)} bytes jpeg")

    status, _ = get("/thumb?path=" + urllib.parse.quote(MUSIC))
    expect("refuses a non-image thumbnail", status == 400, f"status {status}")

    status, body = get("/api/music?folder=" + urllib.parse.quote(
        str(Path(MUSIC).parent)))
    expect("lists a music folder", status == 200, f"status {status}")

    status, _ = get("/api/photos?folder=" + urllib.parse.quote(r"C:\nope"))
    expect("rejects a missing folder", status == 400, f"status {status}")

    # An any-music template must refuse to run without a track.
    status, detail = post("/api/jobs", {
        "template": r"C:\Users\prash\Kiro Projects\BeatCanvas\templates\beat-pulse.json",
        "name": "ui_nomusic", "photo_folder": CARDS, "fill": "repeat",
    })
    expect("any-music template requires a track", status == 400,
           detail[:90] if status != 303 else "accepted, should not have")

    # Reordering: submit a deliberate order and only three photos.
    chosen = [photos[4]["path"], photos[0]["path"], photos[9]["path"]]
    status, _ = post("/api/jobs", {
        "template": r"C:\Users\prash\Kiro Projects\BeatCanvas\templates\beat-pulse.json",
        "name": "ui_anymusic", "photo_folder": CARDS,
        "photo_order": "\n".join(chosen), "music_path": MUSIC,
        "fill": "repeat", "beats_per_clip": "2", "max_seconds": "8",
    })
    expect("any-music job accepted", status == 200, f"status {status}")

    job = wait_done("ui_anymusic")
    expect("any-music job finished", job is not None and job["status"] == "done",
           (job or {}).get("error", "")[:200])
    if job and job["status"] == "done":
        out = job["result"]
        expect("video exists", Path(out["video"]).exists(), out["video"])
        expect("honoured the chosen photo count", out["photos"] == 3,
               str(out["photos"]))
        expect("length near the requested 8s", 6.0 <= out["duration"] <= 9.5,
               f"{out['duration']}s")
        expect("music muxed", bool(out["audio"]))
        print(f"       {out['frames']} frames in {out['elapsed_seconds']}s, "
              f"{out['clips']} cuts, {out['size_mb']}MB")

        status, _ = post("/api/reveal", {"path": out["video"]})
        expect("reveal in folder works", status == 200, f"status {status}")

    status, detail = post("/api/reveal", {"path": r"C:\Windows\win.ini"})
    expect("reveal refuses a path outside output", status == 403,
           f"status {status}")

    # A fixed template should run without any music being chosen.
    status, _ = post("/api/jobs", {
        "template": r"C:\Users\prash\Kiro Projects\BeatCanvas\templates"
                    r"\pendulum-zoom.json",
        "name": "ui_fixed", "photo_folder": CARDS, "fill": "trim", "limit": "",
    })
    expect("fixed template accepted with no music chosen", status == 200,
           f"status {status}")
    job = wait_done("ui_fixed")
    expect("fixed job finished", job is not None and job["status"] == "done",
           (job or {}).get("error", "")[:200])
    if job and job["status"] == "done":
        print(f"       {job['result']['duration']}s, "
              f"{job['result']['clips']} cuts, "
              f"{job['result']['elapsed_seconds']}s to render")

    say("")
    say("UI checks passed" if not failures else f"{failures} failure(s)")
    say(f"(report written to {REPORT})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
