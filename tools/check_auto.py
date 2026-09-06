"""Check the automatic mode end to end, through the running app.

Everything before this proved the pieces work in isolation. What matters to a person is
whether choosing "let the music decide" in the app produces a video, whether the four
paces really differ in the finished file, and whether the reasoning survives into the job
record so a result can be understood after the fact.
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
#: Short on purpose. Four full renders at 1080x1920 with three layers of effects is
#: minutes of work, and twelve seconds is long enough for the paces to separate clearly.
SECONDS = "12"
REPORT = config.ROOT / "_logs" / "auto_report.txt"

PACES = ("slow", "medium", "fast", "accurate")

#: Job names have to be unique per run. Reusing them means the poll below finds a finished
#: job from a previous run and reports its results as if they were this run's, which hides
#: every change made since.
TAG = time.strftime("%H%M%S")

failures = 0
_lines: list[str] = []


def say(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))
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
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, "ok"
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        return 0, str(exc)


def main() -> int:
    status, home = get("/")
    if status != 200:
        say(f"server not answering on {BASE} ({status})")
        return 2

    say("1. the app offers the automatic mode")
    check("there is an option to let the music decide",
          'value="auto"' in home and "Let the music decide" in home)
    check("the pace choice is present", 'name="pace"' in home)
    for pace in PACES:
        check(f"'{pace}' can be chosen", f'value="{pace}"' in home)

    say("\n2. it refuses to run without music, since it has nothing to listen to")
    status, detail = post("/api/jobs", {
        "template": "auto", "name": f"AUTO_{TAG}_nomusic",
        "photo_folder": str(PHOTOS), "fill": "repeat"})
    check("a missing track is rejected", status == 400, detail[:120])

    status, payload = get("/api/photos?folder=" + urllib.parse.quote(str(PHOTOS)))
    pictures = [p["path"] for p in json.loads(payload)["photos"]]
    say(f"\nusing {len(pictures)} photos, {SECONDS}s of "
        f"{Path(MUSIC).name}\n")

    say("3. every pace renders")
    for pace in PACES:
        status, detail = post("/api/jobs", {
            "template": "auto",
            "name": f"AUTO_{TAG}_{pace}",
            "photo_order": "\n".join(pictures),
            "photo_folder": str(PHOTOS),
            "music_path": MUSIC,
            "fill": "repeat",
            "max_seconds": SECONDS,
            "pace": pace,
        })
        check(f"queued {pace}", status in (200, 303), f"{status} {detail[:100]}")

    wanted = {f"AUTO_{TAG}_{p}" for p in PACES}
    results: dict[str, dict] = {}
    notes: dict[str, list] = {}
    deadline = time.time() + 1800
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
                notes[name] = (job.get("result") or {}).get("notes") or []
                say(f"        {name}: done")
            elif job.get("status") in ("failed", "cancelled"):
                results[name] = {"error": job.get("error") or job.get("status")}
                say(f"        {name}: {job.get('status')} "
                    f"- {str(job.get('error'))[:160]}")
        time.sleep(3)

    check("all four finished", len(results) == len(wanted),
          f"{len(results)} of {len(wanted)}")
    good = {n: r for n, r in results.items() if "error" not in r}
    for name, result in sorted(results.items()):
        if "error" in result:
            check(f"{name} rendered", False, str(result["error"])[:200])

    if len(good) < 2:
        say("\nnot enough finished renders to compare")
        say("\n" + "=" * 72)
        say(f"{failures} check(s) FAILED" if failures else "done")
        return 1 if failures else 0

    say("\n4. the paces produce genuinely different edits")
    for name, result in sorted(good.items()):
        say(f"        {name:14s} {result['clips']:3d} photo changes  "
            f"{result['duration']:5.1f}s  {result['size_mb']:5.1f} MB  "
            f"audio={result['audio']}")
    counts = {n: r["clips"] for n, r in good.items()}
    check("no two paces cut the same number of times",
          len(set(counts.values())) == len(counts), str(sorted(counts.values())))
    order = [counts[f"AUTO_{TAG}_{p}"] for p in PACES
             if f"AUTO_{TAG}_{p}" in counts]
    check("busyness rises with the pace chosen",
          all(a <= b for a, b in zip(order, order[1:])),
          " -> ".join(str(v) for v in order))
    slowest, fastest = f"AUTO_{TAG}_slow", f"AUTO_{TAG}_accurate"
    if slowest in counts and fastest in counts:
        check("accurate cuts several times more often than slow",
              counts[fastest] >= counts[slowest] * 2,
              f"slow {counts[slowest]}, accurate {counts[fastest]}")

    say("\n5. every render carries its music and the requested length")
    for name, result in sorted(good.items()):
        check(f"{name} has audio", bool(result["audio"]))
        check(f"{name} is about {SECONDS}s",
              abs(result["duration"] - float(SECONDS)) < 2.5,
              f"{result['duration']:.1f}s")

    say("\n6. the reasoning is recorded with the job")
    for name in sorted(good):
        lines = notes.get(name) or []
        joined = " ".join(lines)
        check(f"{name} says what it heard",
              "BPM" in joined and "heard as" in joined,
              next((line for line in lines if "heard as" in line), "")[:110])
        check(f"{name} says what it decided",
              any(line.startswith("plan:") for line in lines),
              next((line for line in lines if line.startswith("plan:")),
                   "")[:110])

    say("\n   what the director heard and decided")
    for name in sorted(good):
        say(f"\n        {name}")
        for line in (notes.get(name) or []):
            for part in (line[6:].split(" | ") if line.startswith("plan:")
                         else [line]):
                say(f"          {part.strip()[:150]}")

    say("\n" + "=" * 72)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("the automatic mode works end to end")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        say("\nthe check itself crashed:\n" + traceback.format_exc())
        sys.exit(3)
