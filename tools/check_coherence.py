"""Check the edit stays coherent when the music does, and changes when the music does.

This guards the fault found on a real track: the arrival was being rotated on every photo
whether or not anything in the audio had changed, so a steady rhythm produced churning
visuals. The property wanted is not variety for its own sake -- it is that the treatment is
a function of the music. Same situation, same treatment; different situation, different
treatment.

Also checked here: an arrival must finish while its photo is still on screen, and the beat
reactions must actually have beats to fire on at every pace.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from beatcanvas import analysis, choreography, config, effects  # noqa: E402

TRACKS = [
    Path(r"C:\Users\prash\Music\BeatSync\titanium-170190.mp3"),
    Path(r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4"),
]
REPORT = config.ROOT / "_logs" / "coherence_report.txt"

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


def situation(clip, music) -> tuple:
    """The musical facts the treatment is supposed to be a function of."""
    return (clip.section, _voice(clip, music), int(music.energy_at(clip.start) * 3),
            clip.reveal_total > 1)


def _voice(clip, music) -> str:
    inside = [b.voice for b in music.grid
              if clip.start <= b.time < clip.start + clip.duration]
    return max(set(inside), key=inside.count) if inside else "melody"


def main() -> int:
    track = next((t for t in TRACKS if t.exists()), None)
    if track is None:
        say("none of the reference tracks were found")
        return 2

    music = analysis.analyse(track, duration=60.0, fps=30.0)
    say(f"track  : {track.name}")
    say(f"         {music.duration:.1f}s at {music.bpm:.1f} BPM in {music.meter}/4, "
        f"{len(music.sections)} sections, {len(music.bursts)} runs of hits")
    say("")

    for pace in choreography.PACES:
        plan = choreography.compose(music, pace=pace, max_seconds=45.0)
        clips = plan.template.clips
        beat = music.beat_duration
        say(f"{pace} pace: {len(clips)} photo changes")

        # 1. The treatment must be a function of the music.
        seen: dict[tuple, set] = {}
        for clip in clips:
            seen.setdefault(situation(clip, music), set()).add(clip.reveal_kind)
        wobbly = {k: v for k, v in seen.items() if len(v) > 2}
        check(f"{pace}: the same musical situation gets the same arrival",
              not wobbly,
              "; ".join(f"{k} -> {sorted(v)}" for k, v in list(wobbly.items())[:2])
              or f"{len(seen)} distinct situations")

        # 2. Churn: how often the arrival changes from one photo to the next. On a track
        #    whose rhythm barely moves this should be low, not near total.
        if len(clips) > 2:
            switches = sum(1 for a, b in zip(clips, clips[1:])
                           if a.reveal_kind != b.reveal_kind)
            rate = switches / (len(clips) - 1)
            situation_changes = sum(
                1 for a, b in zip(clips, clips[1:])
                if situation(a, music) != situation(b, music))
            allowed = situation_changes / (len(clips) - 1) + 0.25
            check(f"{pace}: the arrival changes no more often than the music does",
                  rate <= allowed,
                  f"arrival changed on {rate:.0%} of cuts, "
                  f"the situation on {situation_changes / (len(clips) - 1):.0%}")

        # 3. An arrival must finish while its photo is on screen.
        overrun = [c for c in clips
                   if c.reveal_kind not in ("", "cut", "pieces")
                   and c.reveal_duration > c.duration + 1e-6]
        check(f"{pace}: every arrival finishes before its photo leaves", not overrun,
              f"{len(overrun)} overrun, worst "
              f"{max((c.reveal_duration / max(c.duration, 1e-6) for c in overrun), default=0):.2f}x"
              if overrun else f"{len(clips)} checked")

        # 4. The reaction layer must have something to fire on.
        silent = [c for c in clips if c.micro and not c.micro_beats]
        check(f"{pace}: every reacting photo has a beat to react on", not silent,
              f"{len(silent)} of {len(clips)} had none")

        # 5. Piece reveals still land on real hits.
        with_pieces = [c for c in clips if c.reveal_total > 1]
        if with_pieces:
            hits = {b.time for b in music.grid} | set(music.percussive_onsets)
            landed = sum(
                1 for c in with_pieces for moment in c.reveal_at
                if any(abs(moment + choreography.EARLY_OFFSET - h) < 0.05 for h in hits))
            total = sum(len(c.reveal_at) for c in with_pieces)
            check(f"{pace}: pieces land on real hits", landed >= total * 0.8,
                  f"{landed} of {total} across {len(with_pieces)} photos")
        say("")

    say("what a steady passage now looks like (accurate pace, first 14 photos)")
    plan = choreography.compose(music, pace="accurate", max_seconds=45.0)
    for line in plan.explain[:14]:
        say(f"        {line}")

    say("\ncounts of each arrival used, by pace")
    for pace in choreography.PACES:
        plan = choreography.compose(music, pace=pace, max_seconds=45.0)
        tally: dict[str, int] = {}
        for clip in plan.template.clips:
            tally[clip.reveal_kind] = tally.get(clip.reveal_kind, 0) + 1
        say(f"        {pace:9s} " + ", ".join(
            f"{k or 'none'}={v}" for k, v in sorted(tally.items(),
                                                    key=lambda kv: -kv[1])))

    say("\n" + "=" * 76)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("the edit follows the music rather than churning independently of it")
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
