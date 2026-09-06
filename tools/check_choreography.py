"""Check the director makes the decisions the design asks for.

The interesting failures here are not crashes, they are bad taste expressed as data: cuts
landing off the bar, the same reveal three times running, a quiet section reacting as hard
as a chorus, or the pace setting making no real difference. Each of those is checked.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from beatcanvas import analysis, choreography, config, effects  # noqa: E402

MUSIC = Path(r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4")
REPORT = config.ROOT / "_logs" / "choreography_report.txt"

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


def main() -> int:
    if not MUSIC.exists():
        say(f"music not found: {MUSIC}")
        return 2

    music = analysis.analyse(MUSIC, duration=90.0, fps=30.0)
    say(f"track  : {MUSIC.name}  {music.duration:.1f}s at {music.bpm:.1f} BPM, "
        f"{len(music.sections)} sections")
    say("")

    plans = {}
    say("1. every pace produces a different edit")
    for name in choreography.PACES:
        plan = choreography.compose(music, pace=name, max_seconds=60.0)
        plans[name] = plan
        clips = plan.template.clips
        say(f"        {name:9s} {len(clips):3d} photo changes  "
            f"reveals used: {len({c.reveal_kind for c in clips})}  "
            f"movements: {len({c.animation for c in clips})}")
    counts = {n: len(p.template.clips) for n, p in plans.items()}
    check("each pace was planned", all(v > 2 for v in counts.values()), str(counts))
    check("slower paces cut less than faster ones",
          counts["slow"] < counts["fast"] <= counts["accurate"],
          f"slow {counts['slow']} < fast {counts['fast']} "
          f"<= accurate {counts['accurate']}")
    check("no two paces produced the same edit",
          len(set(counts.values())) >= 3, str(sorted(counts.values())))

    plan = plans["medium"]
    clips = plan.template.clips

    say("\n2. cuts sit where a listener already feels a change")
    bars = {b.time for b in music.grid if b.is_downbeat}
    beats = {b.time for b in music.grid}
    tolerance = choreography.EARLY_OFFSET + 0.03
    # The first clip is deliberately pinned to the start of the video rather than to the
    # first beat, which usually falls a little later. Honouring the beat instead would open
    # on a black frame, so it is excluded from the on-beat count.
    rest = clips[1:]
    on_bar = sum(1 for c in rest
                 if any(abs(c.start - t) <= tolerance for t in bars))
    on_beat = sum(1 for c in rest
                  if any(abs(c.start - t) <= tolerance for t in beats))
    check("every photo change after the first lands on a beat", on_beat == len(rest),
          f"{on_beat} of {len(rest)}")
    check("the first clip starts the video, not the first beat",
          abs(clips[0].start) < 1e-6, f"{clips[0].start:.3f}s")

    # A bar line is where the slower paces belong. The faster ones are meant to cut on
    # beats, and a chorus is meant to cut on its backbeats, so requiring bar lines
    # everywhere would be requiring the wrong thing.
    slow = choreography.compose(music, pace="slow", max_seconds=60.0).template.clips[1:]
    slow_on_bar = sum(1 for c in slow
                      if any(abs(c.start - t) <= tolerance for t in bars))
    check("at slow pace the changes land on bar lines",
          slow_on_bar >= len(slow) * 0.8,
          f"{slow_on_bar} of {len(slow)} on a bar line")
    say(f"        at medium pace {on_bar} of {len(rest)} land on a bar line; the rest "
        f"are backbeats inside busier sections")

    say("\n3. cuts are pulled early, never late")
    offsets = []
    for clip in clips[1:]:
        nearest = min(beats, key=lambda t: abs(t - clip.start))
        offsets.append(clip.start - nearest)
    worst_late = max(offsets)
    check("no cut lands after its beat", worst_late <= 1e-6,
          f"latest is {worst_late * 1000:+.0f}ms")
    check("cuts are early by roughly the perceptual offset",
          abs(float(np.median(offsets)) + choreography.EARLY_OFFSET) < 0.02,
          f"median {float(np.median(offsets)) * 1000:+.0f}ms, "
          f"intended {-choreography.EARLY_OFFSET * 1000:+.0f}ms")

    say("\n4. no effect repeats itself into predictability")
    runs = _longest_run([c.reveal_kind for c in clips])
    check("the same arrival is never used 3 times running", runs <= 2,
          f"longest run {runs}")
    moves = _longest_run([c.animation for c in clips])
    check("the same movement is never used 3 times running", moves <= 2,
          f"longest run {moves}")

    say("\n5. the section decides the visual language")
    by_section: dict[str, set] = {}
    for clip in clips:
        by_section.setdefault(clip.section, set()).add(clip.reveal_kind)
    calm = ("intro", "outro", "breakdown", "bridge")
    for label, used in sorted(by_section.items()):
        allowed = set(choreography.PALETTES.get(
            label, choreography.PALETTES["verse"]).reveals)
        # A drop always gets the most emphatic arrival available, and any section that is
        # not calm may also build a photo from a run of hits when the music offers one.
        allowed |= {"zoom_burst"}
        if label not in calm:
            allowed |= {"pieces"}
        check(f"{label}: arrivals come from its own palette", used <= allowed,
              ", ".join(sorted(used)))

    say("\n6. quiet sections react less than loud ones")
    levels: dict[str, list[float]] = {}
    for clip in clips:
        levels.setdefault(clip.section, []).append(clip.micro_intensity)
    means = {k: float(np.mean(v)) for k, v in levels.items()}
    say("        " + "  ".join(f"{k}={v:.2f}" for k, v in sorted(means.items())))
    loud = [v for k, v in means.items() if k in ("chorus", "drop", "build")]
    quiet = [v for k, v in means.items()
             if k in ("intro", "outro", "breakdown", "bridge")]
    if loud and quiet:
        check("loud sections react harder than quiet ones",
              float(np.mean(loud)) > float(np.mean(quiet)),
              f"loud {float(np.mean(loud)):.2f} vs quiet {float(np.mean(quiet)):.2f}")
    else:
        say("        (this track has no contrasting sections to compare)")
    check("reactions never reach full strength in a quiet section",
          all(v < 0.7 for k, v in means.items()
              if k in ("intro", "outro", "breakdown")),
          ", ".join(f"{k}={v:.2f}" for k, v in sorted(means.items())))

    say("\n7. arrivals suit the sound that triggered them")
    mismatched = []
    for clip in clips:
        family = effects.family_of(clip.reveal_kind)
        if clip.section in ("intro", "outro", "breakdown", "bridge") \
                and family == effects.PERCUSSIVE and clip.reveal_kind != "cut":
            mismatched.append(f"{clip.start:.1f}s {clip.reveal_kind}")
    check("no hard arrival in a calm section", not mismatched,
          ", ".join(mismatched[:4]))

    say("\n8. durations are proportional to the tempo")
    beat_ms = music.beat_duration * 1000
    for name in ("cut", "scale_pop", "fade"):
        got = effects.duration_for(effects.REVEALS, name, music.beat_duration) * 1000
        want = effects.REVEALS[name].beats * beat_ms
        check(f"'{name}' lasts {effects.REVEALS[name].beats:g} beats",
              abs(got - want) < 1.0, f"{got:.0f}ms at {music.bpm:.0f} BPM")

    say("\n9. piece reveals follow the quick runs in the music")
    with_pieces = [c for c in clips if c.reveal_total > 1]
    if with_pieces:
        exact = all(len(c.reveal_at) == c.reveal_total for c in with_pieces)
        check("one piece per hit of the run", exact,
              f"{len(with_pieces)} photos built from pieces")
        landed = 0
        for clip in with_pieces:
            for moment in clip.reveal_at:
                if any(abs(moment + choreography.EARLY_OFFSET - t) < 0.05
                       for t in beats | set(music.percussive_onsets)):
                    landed += 1
        total = sum(len(c.reveal_at) for c in with_pieces)
        check("pieces land on real hits", landed >= total * 0.8,
              f"{landed} of {total}")
    else:
        say("        (no runs were used at this pace)")

    say("\n10. the plan explains itself")
    check("there is a line per photo", len(plan.explain) == len(clips),
          f"{len(plan.explain)} lines for {len(clips)} photos")
    say("")
    for line in plan.explain[:18]:
        say(f"        {line}")
    if len(plan.explain) > 18:
        say(f"        ... {len(plan.explain) - 18} more")

    say("\n" + "=" * 78)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("the director follows the design's rules")
    return 0


def _longest_run(values: list[str]) -> int:
    best = run = 1
    for previous, current in zip(values, values[1:]):
        run = run + 1 if current == previous else 1
        best = max(best, run)
    return best if values else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        say("\nthe check itself crashed:\n" + traceback.format_exc())
        sys.exit(3)
