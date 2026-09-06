"""Print the full analysis of a tune, and check the loop reasoning holds.

    tools\\run.bat tools\\explain_tune.py "C:\\path\\to\\track.mp3"

With no path it uses the first reference track it can find. The account is written to
_logs\\tune_<name>.txt as well as the console.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from beatcanvas import analysis, choreography, config, explain, rhythm  # noqa: E402

FALLBACKS = [
    Path(r"C:\Users\prash\Music\BeatSync\titanium-170190.mp3"),
    Path(r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4"),
]


def main() -> int:
    if len(sys.argv) > 1:
        track = Path(sys.argv[1].strip('"'))
    else:
        track = next((t for t in FALLBACKS if t.exists()), None)
    if track is None or not track.exists():
        print(f"track not found: {track}")
        return 2

    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    chosen = sys.argv[3] if len(sys.argv) > 3 else "auto"
    if chosen not in analysis.PRESETS:
        print(f"unknown reading {chosen!r}; choose from "
              f"{', '.join(analysis.PRESETS)}")
        return 2
    reading = analysis.preset(chosen)

    music = analysis.analyse(track, duration=seconds, fps=30.0, preset=chosen)
    figures = rhythm.analyse(music, subdivisions=reading.subdivisions,
                             threshold=reading.same_figure,
                             use_loop=reading.use_loop, cohere=reading.cohere)
    account = explain.describe(music, figures, name=track.name)

    out = config.ROOT / "_logs" / f"tune_{track.stem[:40]}_{chosen}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(account, encoding="utf-8")
    try:
        print(account)
    except UnicodeEncodeError:
        print(account.encode("ascii", "replace").decode("ascii"))
    print(f"\nwritten to {out}")

    # The point of the loop work: a looped track must not keep changing its animation.
    print("\n" + "=" * 78)
    print("DOES THE PICTURE STAY STILL WHEN THE MUSIC DOES?")
    print("=" * 78)
    failures = 0
    for pace in ("medium", "accurate"):
        plan = choreography.compose(music, pace=pace, max_seconds=min(seconds or 60.0,
                                                                     60.0),
                                    rhythm_map=figures)
        clips = plan.template.clips
        if len(clips) < 2:
            continue
        switches = sum(1 for a, b in zip(clips, clips[1:])
                       if a.reveal_kind != b.reveal_kind)
        rate = switches / (len(clips) - 1)
        used: dict[int, set] = {}
        for clip in clips:
            if clip.figure >= 0:
                used.setdefault(clip.figure, set()).add(clip.reveal_kind)

        # A figure is allowed its own arrival, its softened counterpart, and a plain cut
        # where a photo was too short to build anything. Anything beyond those three means
        # something other than the figure is choosing.
        spread = {}
        for figure, arrivals in used.items():
            pattern = next((p for p in figures.patterns if p.id == figure), None)
            allowed = {"cut"}
            if pattern is not None and pattern.treatment is not None:
                allowed |= {pattern.treatment.reveal, pattern.treatment.gentle_reveal}
            unexpected = sorted(set(arrivals) - allowed)
            if unexpected:
                spread[figure] = unexpected

        print(f"\n  {pace}: {len(clips)} photos, arrival changed on {rate:.0%} of cuts")
        for figure, arrivals in sorted(used.items()):
            print(f"    figure {figure} -> {', '.join(sorted(arrivals))}")
        if figures.looped and rate > 0.5:
            print(f"    [FAIL] a looped track should not change arrival on "
                  f"{rate:.0%} of cuts")
            failures += 1
        if spread:
            print(f"    [FAIL] a figure used more than one arrival: {spread}")
            failures += 1
        if not spread and not (figures.looped and rate > 0.5):
            print("    [PASS] each figure keeps one arrival throughout")

    print("")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(3)
