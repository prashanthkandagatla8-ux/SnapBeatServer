"""Check the two styles that carry their own music.

These store the track as a bare file name so the style stays portable, which means the
file has to be found on load. When that lookup fails the style cannot render at all, so
it is worth a test of its own.
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
from beatcanvas.template import Template  # noqa: E402

BASE = "http://127.0.0.1:8772"
PHOTOS = config.ROOT / "_placeholders"
REPORT = config.ROOT / "_logs" / "fixed_report.txt"

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
            return response.status, "ok"
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        return 0, str(exc)


def main() -> int:
    say("1. the music that belongs to each style can be found")
    fixed: list[Template] = []
    for path in sorted(config.TEMPLATE_DIR.glob("*.json")):
        template = Template.load(path)
        if template.music_mode != "fixed":
            continue
        fixed.append(template)
        resolved = Path(template.audio_path)
        check(f"{template.name}: music located", resolved.exists(),
              str(resolved))
        check(f"{template.name}: path is absolute", resolved.is_absolute(),
              str(resolved))

    check("both music-attached styles are present", len(fixed) == 2, str(len(fixed)))
    check("a copy of the track travels with the templates",
          any(config.TEMPLATE_MUSIC_DIR.glob("*")),
          str(config.TEMPLATE_MUSIC_DIR))

    status, home = get("/")
    if status != 200:
        say(f"\nserver not answering on {BASE}; skipping the render checks")
        say("\n" + "=" * 60)
        say(f"{failures} check(s) FAILED" if failures else "all checks passed")
        return 1 if failures else 0

    say("\n2. each one renders without any music being chosen")
    names = {}
    for template in fixed:
        job_name = "FIX_" + template.name.replace(" ", "_").replace("+", "and")
        names[job_name] = template.name
        status, detail = post("/api/jobs", {
            "template": str(config.TEMPLATE_DIR / f"{_slug(template.name)}.json"),
            "name": job_name,
            "photo_folder": str(PHOTOS),
            "fill": "repeat",
        })
        check(f"queued {template.name}", status in (200, 303),
              f"{status} {detail[:120]}")

    results: dict[str, dict] = {}
    deadline = time.time() + 900
    while time.time() < deadline and len(results) < len(names):
        status, payload = get("/api/jobs")
        if status != 200:
            time.sleep(3)
            continue
        for job in json.loads(payload)["jobs"]:
            name = job.get("name")
            if name not in names or name in results:
                continue
            if job.get("status") == "done":
                results[name] = job.get("result") or {}
                say(f"        {names[name]}: done")
            elif job.get("status") in ("failed", "cancelled"):
                results[name] = {"error": job.get("error") or job.get("status")}
                say(f"        {names[name]}: {job.get('status')}")
        time.sleep(3)

    check("both finished", len(results) == len(names), f"{len(results)} of {len(names)}")
    for name, result in sorted(results.items()):
        if "error" in result:
            check(f"{names[name]} rendered", False, str(result["error"])[:200])
            continue
        check(f"{names[name]} rendered", True,
              f"{result['clips']} clips, {result['duration']}s, "
              f"{result['size_mb']} MB")
        check(f"{names[name]} kept its own music", bool(result["audio"]))
        check(f"{names[name]} produced a file", Path(result["video"]).exists(),
              result["video"])

    say("\n" + "=" * 60)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("both music-attached styles work")
    return 0


def _slug(name: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in name]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        say("\nthe check itself crashed:\n" + traceback.format_exc())
        sys.exit(3)
