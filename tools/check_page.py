"""Report what the running server is actually serving, and where it is serving it from.

A stale process holding the port is the usual reason a change does not appear: run.py
reuses an existing listener, so starting it again can leave the old code answering.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402

OUT = config.ROOT / "_logs" / "page_report.txt"
lines: list[str] = []


def say(text: str) -> None:
    print(text)
    lines.append(text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


MARKERS = {
    "the automatic mode option": 'value="auto"',
    "its label": "Let the music decide",
    "the pace control": 'name="pace"',
    "pace: slow": 'value="slow"',
    "pace: accurate": 'value="accurate"',
    "the pace group wrapper": "autoOnly",
    "reading choice": 'name="analysis"',
    "pace is remembered": "'pace', 'analysis', 'look'",
    "animation style choice": 'name="look"',
    "style: grids only": 'value="grids"',
    "style: circles only": 'value="circles"',
    "style: premium": 'value="premium"',
    "reading: one groove": 'value="one-groove"',
    "reading: half time": 'value="half-time"',
    "frame shape control": 'name="frame"',
    "16:9 option": 'value="landscape"',
    "piece shape control": 'name="reveal_shape"',
}

try:
    with urllib.request.urlopen("http://127.0.0.1:8772/", timeout=30) as reply:
        page = reply.read().decode("utf-8", "replace")
    say(f"served page: {len(page)} bytes")
except Exception as exc:
    say(f"could not reach the server: {exc}")
    raise SystemExit(2)

say("")
say("what the SERVER is sending:")
missing = []
for label, marker in MARKERS.items():
    present = marker in page
    say(f"  [{'yes' if present else 'NO '}] {label}   ({marker})")
    if not present:
        missing.append(label)

source = config.ROOT / "beatcanvas" / "web" / "templates" / "index.html"
text = source.read_text(encoding="utf-8")
say("")
say(f"what the FILE on disk contains: {source}")
for label, marker in MARKERS.items():
    say(f"  [{'yes' if marker in text else 'NO '}] {label}")

say("")
if missing:
    say("The file has the change but the server is not sending it, which means an older "
        "process still holds port 8772. Stop every python process and start run.py again."
        if all(m in text for m in MARKERS.values())
        else "The change is missing from the file on disk too.")
else:
    say("The server is sending the new controls. If the browser still shows the old "
        "page, it is cached: reload with Ctrl+F5.")
