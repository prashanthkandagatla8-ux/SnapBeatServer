"""Check the two promises this project is built on.

**A repeated figure is answered the same way.** The rhythm map must find figures that
actually recur, give each exactly one treatment, and use that treatment everywhere the
figure appears -- for the whole song, not per section.

**A photo is never gone before it was seen.** For every photo there must be a real stretch
of frames where it is fully arrived: nothing masked, nothing faded, nothing blurred. This
is measured on rendered frames rather than inferred from the plan, because the plan
promising it and the renderer delivering it are two different things.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from beatcanvas import (analysis, choreography, config,  # noqa: E402
                        photos as photo_mod, renderer, rhythm)

TRACKS = [
    Path(r"C:\Users\prash\Music\BeatSync\titanium-170190.mp3"),
    Path(r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4"),
]
CARDS = config.ROOT / "_placeholders"
REPORT = config.ROOT / "_logs" / "rhythm_report.txt"

WIDTH, HEIGHT = 270, 480

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


def sparkline(values) -> str:
    ramp = " .:-=+*#%@"
    peak = max(values) if len(values) else 0
    if peak <= 0:
        return " " * len(values)
    top = len(ramp) - 1
    return "".join(ramp[min(top, int(round(v / peak * top)))] for v in values)


def main() -> int:
    track = next((t for t in TRACKS if t.exists()), None)
    if track is None:
        say("none of the reference tracks were found")
        return 2
    cards = photo_mod.collect(CARDS)
    if len(cards) < 2:
        say("need placeholder photos")
        return 2

    music = analysis.analyse(track, duration=60.0, fps=30.0)
    figures = rhythm.analyse(music)

    say(f"track  : {track.name}")
    say(f"         {music.duration:.1f}s at {music.bpm:.1f} BPM in {music.meter}/4, "
        f"{len(rhythm.bar_windows(music))} bars")
    for note in figures.notes:
        say(f"         {note}")
    say("")

    say("1. the track really does reduce to a few repeating figures")
    check("figures were found", len(figures.patterns) > 0, str(len(figures.patterns)))
    check("there are few enough to be a language, not a list",
          1 <= len(figures.patterns) <= 8, f"{len(figures.patterns)} figures")
    recurring = [p for p in figures.patterns if p.occurrences > 1]
    check("at least one figure recurs", bool(recurring),
          f"{len(recurring)} of {len(figures.patterns)} recur")
    covered = sum(p.occurrences for p in recurring)
    total_bars = sum(p.occurrences for p in figures.patterns)
    check("most of the song is made of recurring figures",
          total_bars and covered >= total_bars * 0.6,
          f"{covered} of {total_bars} bars")

    say("\n   the figures, and what each one gets")
    for pattern in figures.patterns:
        say(f"        {pattern.describe()}")
        say(f"          shape |{sparkline(pattern.signature)}|")
        say(f"          bars  {pattern.bars[:14]}"
            + (" ..." if len(pattern.bars) > 14 else ""))
        if pattern.treatment:
            say(f"          gets  {pattern.treatment.reveal} + "
                f"{pattern.treatment.movement} + {pattern.treatment.transition}")
            say(f"          why   {pattern.treatment.because}")

    say("\n2. one treatment per figure, used everywhere it appears")
    for pattern in figures.patterns:
        check(f"figure {pattern.id} has exactly one treatment",
              pattern.treatment is not None)
    say("")

    for pace in ("medium", "accurate"):
        plan = choreography.compose(music, pace=pace, width=WIDTH, height=HEIGHT,
                                    max_seconds=45.0, rhythm_map=figures)
        clips = plan.template.clips
        say(f"3. {pace} pace: the edit follows the figures ({len(clips)} photos)")

        # Which arrivals were used for each figure, across the whole edit.
        # Read the figure the director recorded on each clip. Recomputing it from the
        # clip's start time disagrees near bar lines, because a clip starts earlier than
        # the beat it was decided on and the first one is pinned to zero.
        used: dict[int, set] = {}
        for clip in clips:
            if clip.figure >= 0:
                used.setdefault(clip.figure, set()).add(clip.reveal_kind)
        # A figure has exactly two permitted arrivals: its own, and its own softened for a
        # calm passage. Anything else means something other than the figure is deciding.
        bad = {}
        for pattern in figures.patterns:
            allowed = set()
            if pattern.treatment is not None:
                allowed = {pattern.treatment.reveal, pattern.treatment.gentle_reveal,
                           "cut"}
            seen = used.get(pattern.id, set())
            if not seen <= allowed:
                bad[pattern.id] = sorted(seen - allowed)
        check(f"{pace}: each figure keeps its own arrival across the song", not bad,
              "; ".join(f"figure {k}: unexpected {v}" for k, v in bad.items())
              or "; ".join(f"figure {k}: {sorted(v)}" for k, v in sorted(used.items())))

        say(f"\n4. {pace} pace: every photo is fully seen")
        sized = plan.template.adapt(len(cards), "repeat")
        photos = photo_mod.PhotoSet(cards, (WIDTH, HEIGHT),
                                    renderer.required_headroom(sized))
        worst_name, worst_window = "", 1e9
        unseen = []
        for clip in sized.clips:
            window = _full_view_window(sized, photos, clip)
            if window < choreography.MIN_FULL_VIEW - 0.05:
                unseen.append(f"{clip.start:.2f}s only {window:.2f}s")
            if window < worst_window:
                worst_window, worst_name = window, f"{clip.start:.2f}s"
        check(f"{pace}: no photo leaves before it was seen whole", not unseen,
              f"worst is {worst_name} with {worst_window:.2f}s"
              if not unseen else "; ".join(unseen[:4]))
        check(f"{pace}: the shortest full view meets the promise",
              worst_window >= choreography.MIN_FULL_VIEW - 0.05,
              f"{worst_window:.2f}s against a promised "
              f"{choreography.MIN_FULL_VIEW:.2f}s")

        held = [n for n in plan.notes if "dropped so the photo" in n]
        if held:
            say(f"        {held[0][:150]}")
        say("")

    say("=" * 78)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("repeated figures get one treatment each, and every photo is seen whole")
    return 0


def _full_view_window(template, photos, clip) -> float:
    """How long this photo is on screen fully arrived and unobstructed.

    Measured against the photo's own settled appearance: the frame at the end of the clip,
    which is what it looks like once everything has finished. Any earlier frame that
    matches it closely is a frame where the photo is fully visible. This catches a mask
    still closing, a fade still rising or a blur still clearing, without having to know
    which of those was in play.
    """
    fps = template.fps or 30.0
    settled = renderer.render_frame(
        template, photos, max(clip.start, clip.end - 1.0 / fps))
    if clip.reveal_total > 1:
        # A piece reveal is only whole once every piece has landed.
        earliest = max(clip.reveal_at) if clip.reveal_at else clip.start
    else:
        earliest = clip.start

    step = 1.0 / fps
    time_s = earliest
    first_whole = None
    while time_s < clip.end:
        frame = renderer.render_frame(template, photos, time_s)
        difference = float(np.abs(frame.astype(np.int16)
                                  - settled.astype(np.int16)).mean())
        # Movement continues through the clip, so an exact match is not expected; what is
        # being detected is the absence of an arrival, which changes the frame far more.
        if difference < 6.0:
            first_whole = time_s
            break
        time_s += step
    if first_whole is None:
        return 0.0
    return max(0.0, clip.end - first_whole)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        say("\nthe check itself crashed:\n" + traceback.format_exc())
        sys.exit(3)
