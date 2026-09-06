"""Report tempo and metre for every track available, so readings can be sanity-checked.

Tempo and metre are the two things everything downstream rests on, and they are the two
things this environment cannot verify automatically: there is no ground truth to compare
against. So this prints them plainly for a person who knows the songs to check by ear.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from beatcanvas import analysis, config  # noqa: E402

FOLDERS = [Path(r"C:\Users\prash\Music"), Path(r"C:\Users\prash\Music\BeatSync")]
AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".mp4"}
REPORT = config.ROOT / "_logs" / "tracks_report.txt"

lines: list[str] = []


def say(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))
    lines.append(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


found: list[Path] = []
for folder in FOLDERS:
    if folder.is_dir():
        found += [p for p in sorted(folder.iterdir())
                  if p.is_file() and p.suffix.lower() in AUDIO]

if not found:
    say("no audio found")
    raise SystemExit(2)

say(f"{len(found)} track(s). Reading 45 seconds of each.")
say("")
say(f"{'track':52s} {'tempo':>7s} {'metre':>6s} {'bar':>6s}  sections")
say("-" * 100)

for path in found[:12]:
    try:
        music = analysis.analyse(path, duration=45.0, fps=30.0)
    except Exception as exc:
        say(f"{path.name[:52]:52s}  could not read: {type(exc).__name__}")
        continue
    bar = music.beat_duration * music.meter
    labels = ", ".join(dict.fromkeys(s.label for s in music.sections))
    say(f"{path.name[:52]:52s} {music.bpm:6.1f}  {music.meter:d}/4  "
        f"{bar:5.2f}s  {labels}")
    downs = [b.strength for b in music.grid if b.is_downbeat]
    others = [b.strength for b in music.grid if not b.is_downbeat]
    if downs and others:
        say(f"{'':52s}        bar ones average {float(np.mean(downs)):.3f} "
            f"against {float(np.mean(others)):.3f} elsewhere, "
            f"{len(music.bursts)} runs of hits")

say("")
say("Tempo and metre cannot be checked automatically here, so check them by ear: tap "
    "along and see whether the bar length above matches where you feel the '1'. A wrong "
    "metre puts the photo changes in the wrong places.")
