"""Drive a real job through the running server: submit, preview, approve, render.

This is the one path the other checks cannot cover, because it involves the background
worker and the approval gate rather than just request handling.
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
TEMPLATE = r"C:\Users\prash\Kiro Projects\BeatCanvas\templates\0817-1_rebuild.json"
PHOTOS = r"C:\Users\prash\Kiro Projects\BeatCanvas\_placeholders"

failures = 0


def expect(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures += 1


def post_form(path: str, fields: dict) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(BASE + path, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            return response.status, response.geturl()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def get_json(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as response:
        return json.loads(response.read().decode())


def wait_for(job_id: int, statuses: set[str], timeout: float = 240.0) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        jobs = get_json("/api/jobs")["jobs"]
        job = next((j for j in jobs if j["id"] == job_id), None)
        if job is None:
            time.sleep(1.0)
            continue
        if job["status"] != last:
            print(f"       status={job['status']:18s} {job['stage']}")
            last = job["status"]
        if job["status"] in statuses:
            return job
        time.sleep(1.5)
    raise TimeoutError(f"job {job_id} did not reach {statuses} in {timeout}s")


def main() -> int:
    if not Path(TEMPLATE).exists():
        print(f"template missing: {TEMPLATE}")
        return 2

    print("submitting a job with 3 photos and fill=trim ...")
    status, location = post_form("/api/jobs", {
        "template": TEMPLATE,
        "name": "flowcheck",
        "photo_folder": PHOTOS,
        "photo_order": "",
        "fill": "trim",
        "direction": "up",
        "intensity": "1.0",
        "limit": "3",
        "output_dir": r"C:\Users\prash\Kiro Projects\BeatCanvas\output",
    })
    expect("job submitted", status == 200, f"status {status}")
    if status != 200:
        print(location[:400])
        return 1

    job_id = int(str(location).rstrip("/").split("/")[-1])
    print(f"       job id {job_id}")

    job = wait_for(job_id, {"awaiting_approval", "failed"})
    expect("preview completed and stopped for approval",
           job["status"] == "awaiting_approval", job.get("error", "")[:200])
    if job["status"] != "awaiting_approval":
        return 1

    preview = job["result"].get("preview", {})
    expect("preview video exists", Path(preview.get("video", "")).exists(),
           preview.get("video", ""))
    expect("contact sheet exists", Path(preview.get("sheet", "")).exists())
    expect("motion strip exists", Path(preview.get("motion", "")).exists())
    expect("trim reduced the clip count", preview.get("clips", 99) == 3,
           f"{preview.get('clips')} clips, {preview.get('duration')}s")
    expect("photo count honoured", preview.get("photos") == 3,
           str(preview.get("photos")))
    print(f"       estimated full render {preview.get('estimated_full_seconds')}s")

    print("\napproving ...")
    status, _ = post_form(f"/api/jobs/{job_id}/approve", {})
    expect("approve accepted", status == 200, f"status {status}")

    job = wait_for(job_id, {"done", "failed"})
    expect("full render finished", job["status"] == "done", job.get("error", "")[:300])

    output = job["result"].get("output", {})
    video = Path(output.get("video", ""))
    expect("final video exists", video.exists(), str(video))
    if video.exists():
        expect("final video is a plausible size", video.stat().st_size > 50_000,
               f"{video.stat().st_size // 1024}KB")
    expect("audio muxed into the final", bool(output.get("audio")))
    print(f"       {output.get('frames')} frames in {output.get('elapsed_seconds')}s "
          f"({(output.get('seconds_per_frame') or 0) * 1000:.0f} ms/frame)")

    print("\nchecking that a 1-photo job is refused ...")
    status, location = post_form("/api/jobs", {
        "template": TEMPLATE, "name": "onephoto", "photo_folder": PHOTOS,
        "fill": "repeat", "direction": "up", "intensity": "1.0", "limit": "1",
    })
    if status == 200:
        bad_id = int(str(location).rstrip("/").split("/")[-1])
        bad = wait_for(bad_id, {"failed", "awaiting_approval"}, timeout=90)
        expect("a single photo fails with a clear message",
               bad["status"] == "failed" and "at least" in bad.get("error", ""),
               bad.get("error", "")[-120:].strip())
    else:
        expect("a single photo is rejected up front", status == 400, f"status {status}")

    print()
    print("job flow OK" if not failures else f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
