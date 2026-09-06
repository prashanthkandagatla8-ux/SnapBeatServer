"""Print the newest jobs straight from the running server, for a quick look."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402

OUT = config.ROOT / "_logs" / "peek.txt"
lines: list[str] = []
try:
    with urllib.request.urlopen("http://127.0.0.1:8772/api/jobs", timeout=30) as reply:
        jobs = json.loads(reply.read().decode())["jobs"]
    for job in jobs[:10]:
        result = job.get("result") or {}
        lines.append(
            f"#{job['id']} {job['name']:16s} {job['status']:9s} "
            f"{str(job.get('stage'))[:34]:34s} "
            f"template={job.get('template')}")
        if job.get("error"):
            lines.append(f"     error: {str(job['error'])[:400]}")
        if result:
            lines.append(f"     clips={result.get('clips')} "
                         f"duration={result.get('duration')} "
                         f"audio={result.get('audio')}")
except Exception as exc:
    lines.append(f"could not reach the server: {exc}")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
