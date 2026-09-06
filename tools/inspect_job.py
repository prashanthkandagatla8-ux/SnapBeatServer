"""Show, in full, what the director decided for one job.

    tools\\run.bat tools\\inspect_job.py titanium

Matches on part of the job name. Prints the options it was given and every line of the
plan, so a complaint about the result can be traced back to a decision.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402

needle = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
OUT = config.ROOT / "_logs" / "inspect_job.txt"
lines: list[str] = []


def say(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))
    lines.append(text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


database = config.ROOT / "jobs" / "beatcanvas.db"
connection = sqlite3.connect(str(database))
connection.row_factory = sqlite3.Row
rows = connection.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 60").fetchall()

matches = [dict(r) for r in rows
           if needle in str(r["name"]).lower()
           or needle in str(r["options"]).lower()]
if not matches:
    say(f"no job matching '{needle}'. The most recent jobs are:")
    for row in rows[:15]:
        say(f"  #{row['id']} {row['name']} ({row['status']})")
    raise SystemExit(1)

for job in matches[:3]:
    say("=" * 78)
    say(f"#{job['id']} {job['name']}   {job['status']} / {job['stage']}")
    say(f"template: {job['template']}")
    if job.get("error"):
        say(f"error: {job['error']}")

    options = json.loads(job["options"]) if job["options"] else {}
    photos = options.pop("photo_order", [])
    say("\noptions given:")
    for key, value in sorted(options.items()):
        say(f"  {key:16s} {str(value)[:110]}")
    say(f"  {'photos':16s} {len(photos)} supplied")

    result = json.loads(job["result"]) if job["result"] else {}
    if result:
        say(f"\nresult: {result.get('clips')} photo changes, "
            f"{result.get('duration')}s, {result.get('frames')} frames, "
            f"{result.get('size_mb')} MB, audio={result.get('audio')}")
        say(f"        {result.get('video')}")

        say("\nwhat it heard:")
        for note in result.get("notes") or []:
            if not note.startswith("plan:"):
                say(f"  {note}")

        say("\nwhat it decided, per photo:")
        for note in result.get("notes") or []:
            if note.startswith("plan:"):
                for part in note[5:].split(" | "):
                    say(f"  {part.strip()}")
    say("")
