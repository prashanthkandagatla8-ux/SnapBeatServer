"""Render a watchable sample through the same path the app uses, for one reading.

    tools\\run.bat tools\\make_sample.py "<track>" <seconds> <reading> [photo folder]

The point is to end up with a file you can actually look at, produced by the same
analysis -> figures -> choreography -> renderer chain the job queue runs, so what you see
is what the app would make rather than what a test harness would.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import (analysis, choreography, config, effects, explain,  # noqa: E402
                        photos as photo_mod, renderer, rhythm)

DEFAULT_PHOTOS = [
    Path(r"C:\Users\prash\Pictures"),
    config.ROOT / "samples",
]


def say(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def main() -> int:
    if len(sys.argv) < 2:
        say(__doc__ or "")
        return 2
    track = Path(sys.argv[1].strip('"'))
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    name = sys.argv[3] if len(sys.argv) > 3 else "auto"
    style = sys.argv[5] if len(sys.argv) > 5 else "mix"
    if not track.exists():
        say(f"track not found: {track}")
        return 2
    if name not in analysis.PRESETS:
        say(f"unknown reading {name!r}; choose from {', '.join(analysis.PRESETS)}")
        return 2

    folders = ([Path(sys.argv[4].strip('"'))] if len(sys.argv) > 4 else DEFAULT_PHOTOS)
    pictures: list[Path] = []
    for folder in folders:
        if folder.exists():
            pictures = photo_mod.collect(folder)
        if len(pictures) >= 4:
            break
    if len(pictures) < 4:
        say(f"need at least 4 photos; looked in {', '.join(str(f) for f in folders)}")
        return 2

    reading = analysis.preset(name)
    say(f"track   : {track.name}")
    say(f"reading : {name} - {reading.note}")
    say(f"photos  : {len(pictures)} from {pictures[0].parent}")

    started = time.time()
    music = analysis.analyse(track, duration=seconds + 6.0, fps=30.0, preset=name)
    figures = rhythm.analyse(music, subdivisions=reading.subdivisions,
                             threshold=reading.same_figure, use_loop=reading.use_loop,
                             cohere=reading.cohere, look=style)
    plan = choreography.compose(music, pace="medium", max_seconds=seconds,
                                rhythm_map=figures)
    say(f"heard   : {music.bpm:.1f} BPM in {music.meter}/4, bar "
        f"{music.beat_duration * music.meter:.2f}s, "
        f"{len(figures.patterns)} figure(s), looped={figures.looped}")

    template = plan.template
    template.audio_path = str(track)

    # The same last word the worker gives the look, for the same reason: piece shape and
    # transitions are not part of a figure's treatment.
    chosen_look = effects.look(style)
    if chosen_look.shape:
        template.reveal_shape = chosen_look.shape
        for clip in template.clips:
            clip.reveal_shape = chosen_look.shape
    if chosen_look.transitions:
        for clip in template.clips:
            if clip.transition not in chosen_look.transitions:
                clip.transition = chosen_look.transitions[0]

    arrivals = sorted({c.reveal_kind for c in template.clips})
    say(f"look    : {style} - allows {', '.join(chosen_look.reveals) or 'everything'}")
    say(f"edit    : {len(template.clips)} photos over {template.duration:.1f}s, "
        f"arrival(s) used: {', '.join(arrivals)}")

    out = config.OUTPUT_DIR / f"SAMPLE_{name}_{style}_{track.stem[:20]}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    # The renderer wants a PhotoSet rather than a list of paths, because a clip is on screen
    # for many frames and each picture is decoded and resized once for the whole render.
    adapted = template.adapt(len(pictures), "repeat")
    photoset = photo_mod.PhotoSet([str(p) for p in pictures],
                                  (adapted.width, adapted.height),
                                  renderer.required_headroom(adapted))
    renderer.render(adapted, photoset, out)
    say(f"written : {out}  ({time.time() - started:.0f}s)")

    report = config.ROOT / "_logs" / f"tune_{track.stem[:40]}_{name}.txt"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(explain.describe(music, figures, name=track.name),
                      encoding="utf-8")
    say(f"account : {report}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(3)
