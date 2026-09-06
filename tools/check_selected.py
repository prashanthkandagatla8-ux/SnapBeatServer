"""End-to-end proof that the style you pick is the style you get.

Submits the same photos and the same track against three different styles, deliberately
sending the stray reveal_tiles value that the old page sent on every job. Each render must
still come out different, and only the reveal style may use tiles.
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
SECONDS = "8"
REPORT = config.ROOT / "_logs" / "selected_report.txt"

CASES = [
    ("SEL_cut", "beat-cut.json", ""),
    ("SEL_drift", "slow-drift.json", ""),
    ("SEL_reveal", "reveal-tiles.json", ""),
    # Same style as the first, but asked for widescreen, so the only difference in the
    # finished file should be its shape.
    ("SEL_wide", "beat-cut.json", "landscape"),
]

#: What each frame choice must come out as.
EXPECTED_SIZE = {"": (1080, 1920), "landscape": (1920, 1080)}

failures = 0
_lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    _lines.append(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(_lines) + "\n", encoding="utf-8")


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    say(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures += 1


def get(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        return 0, str(exc)


def post(path: str, fields: dict) -> tuple[int, str]:
    body = urllib.parse.urlencode(fields, doseq=True).encode()
    request = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        return 0, str(exc)


def probe_size(path: Path) -> tuple[int, int]:
    """Ask ffprobe what shape the finished file actually is."""
    import subprocess
    result = subprocess.run(
        [config.FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True)
    parts = result.stdout.strip().split("x")
    if len(parts) != 2:
        return (0, 0)
    return int(parts[0]), int(parts[1])


def main() -> int:
    status, home = get("/")
    if status != 200:
        say(f"server not answering on {BASE} ({status}: {home[:200]})")
        return 2

    say("1. the page carries the fix")
    check("options are disabled when they do not apply",
          "function setGroup" in home and "field.disabled" in home)
    check("each style advertises its own beats per photo", "data-beats=" in home)

    status, payload = get("/api/photos?folder=" + urllib.parse.quote(str(PHOTOS)))
    if status != 200:
        say(f"could not list {PHOTOS}: {status} {payload[:200]}")
        return 2
    pictures = [p["path"] for p in json.loads(payload)["photos"]]
    say(f"\nusing {len(pictures)} photos and {Path(MUSIC).name}, {SECONDS}s each\n")

    say("2. submitting the same inputs to different styles and shapes")
    for job_name, template, frame in CASES:
        fields = {
            "template": str(config.TEMPLATE_DIR / template),
            "name": job_name,
            "photo_order": "\n".join(pictures),
            "photo_folder": str(PHOTOS),
            "music_path": MUSIC,
            "fill": "repeat",
            "max_seconds": SECONDS,
            "frame": frame,
            # The value the old page sent for every style, regardless of the choice.
            # It must no longer be able to change what gets rendered.
            "reveal_tiles": "9",
            "beats_per_clip": "1",
        }
        status, body = post("/api/jobs", fields)
        check(f"queued {job_name}", status in (200, 303), f"{status} {body[:120]}")

    say("\n3. waiting for the renders")
    wanted = {name for name, _, _ in CASES}
    results: dict[str, dict] = {}
    saved_options: dict[str, dict] = {}
    deadline = time.time() + 900
    while time.time() < deadline and len(results) < len(wanted):
        status, payload = get("/api/jobs")
        if status != 200:
            time.sleep(3)
            continue
        for job in json.loads(payload)["jobs"]:
            name = job.get("name")
            if name not in wanted or name in results:
                continue
            if job.get("status") == "done":
                results[name] = job.get("result") or {}
                saved_options[name] = job.get("options") or {}
                say(f"        {name}: done")
            elif job.get("status") in ("failed", "cancelled"):
                results[name] = {"error": job.get("error") or job.get("status")}
                saved_options[name] = job.get("options") or {}
                say(f"        {name}: {job.get('status')}")
        time.sleep(3)

    check("all of them finished", len(results) == len(wanted),
          f"{len(results)} of {len(wanted)}")
    for name, result in results.items():
        if "error" in result:
            check(f"{name} rendered", False, str(result["error"])[:160])

    good = {n: r for n, r in results.items() if "error" not in r}
    if len(good) < 2:
        say("\nnot enough finished renders to compare")
        say("\n" + "=" * 60)
        say(f"{failures} check(s) FAILED" if failures else "done")
        return 1 if failures else 0

    say("\n4. the renders are genuinely different")
    for name, result in sorted(good.items()):
        say(f"        {name:12s} {result['clips']:3d} clips  "
            f"{result['duration']:5.1f}s  {result['photos']:2d} photos  "
            f"{result['size_mb']:5.1f} MB  audio={result['audio']}")

    # SEL_wide is the same style as SEL_cut, so its timing is meant to match; only its
    # shape differs. Comparing timing fingerprints therefore excludes it.
    styles = {n: r for n, r in good.items() if n != "SEL_wide"}
    prints = {n: (r["clips"], r["duration"]) for n, r in styles.items()}
    check("no two styles produced the same edit",
          len(set(prints.values())) == len(prints),
          f"{len(set(prints.values()))} distinct of {len(prints)}")

    say("\nthe frame shape asked for is the shape delivered")
    for job_name, _, frame in CASES:
        expected = EXPECTED_SIZE.get(frame)
        result = good.get(job_name)
        if expected is None or result is None:
            continue
        actual = probe_size(Path(result["video"]))
        check(f"{job_name} is {expected[0]}x{expected[1]}", actual == expected,
              f"{actual[0]}x{actual[1]}")

    if "SEL_cut" in good and "SEL_drift" in good:
        check("Slow Drift cuts less often than Beat Cut",
              good["SEL_drift"]["clips"] < good["SEL_cut"]["clips"],
              f"{good['SEL_drift']['clips']} vs {good['SEL_cut']['clips']}")

    say("\n5. the stray tile count was discarded where it does not belong")
    for name, options in sorted(saved_options.items()):
        expects = name == "SEL_reveal"
        present = "reveal_tiles" in options
        check(f"{name}: tile count {'kept' if expects else 'dropped'}",
              present == expects, f"options={sorted(options)}")

    say("\n6. every render has its music")
    for name, result in sorted(good.items()):
        check(f"{name} has audio", bool(result["audio"]))

    say("\n" + "=" * 60)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("the style you pick is the style you get")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        # Console output here is unreliable, so the reason has to reach the report.
        import traceback
        say("\nthe check itself crashed:\n" + traceback.format_exc())
        sys.exit(3)
