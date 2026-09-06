"""Check that each analysis preset really reads the track differently.

A dropdown of seven names is worthless if five of them produce the same numbers, so this
prints the reading each one arrives at side by side: tempo, metre, bar length, how many
figures the bars fall into, and how often the animation would change. Run it after
touching analysis.PRESETS.

    tools\\run.bat tools\\check_presets.py ["C:\\path\\to\\track.mp3"]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import analysis, choreography, effects, rhythm  # noqa: E402

FALLBACKS = [
    Path(r"C:\Users\prash\Music\BeatSync\titanium-170190.mp3"),
    Path(r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4"),
]
SECONDS = 45.0


def say(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def main() -> int:
    tracks = ([Path(a.strip('"')) for a in sys.argv[1:]]
              if len(sys.argv) > 1 else [t for t in FALLBACKS if t.exists()])
    tracks = [t for t in tracks if t.exists()]
    if not tracks:
        say("no track to check against")
        return 2

    say("1. the app still imports with the reading wired in")
    import beatcanvas.app  # noqa: F401
    say("   [PASS] beatcanvas.app imports")

    say("\n2. every preset is reachable and describes itself")
    problems = 0
    for name, item in analysis.PRESETS.items():
        if item.name != name:
            say(f"   [FAIL] {name} is keyed as {item.name}")
            problems += 1
        if len(item.note) < 40:
            say(f"   [FAIL] {name} does not explain itself")
            problems += 1
    say(f"   [{'PASS' if not problems else 'FAIL'}] "
        f"{len(analysis.PRESETS)} presets: {', '.join(analysis.PRESETS)}")

    for track in tracks:
        say(f"\n3. readings of {track.name} (first {SECONDS:.0f}s)")
        say(f"   {'preset':12s} {'BPM':>6s} {'metre':>5s} {'bar':>6s} "
            f"{'figures':>7s} {'loop':>5s} {'photos':>6s} {'changes':>7s}")
        seen: dict[tuple, list[str]] = {}
        rates: dict[str, tuple[float, int]] = {}
        for name, item in analysis.PRESETS.items():
            music = analysis.analyse(track, duration=SECONDS, fps=30.0, preset=name)
            figures = rhythm.analyse(music, subdivisions=item.subdivisions,
                                     threshold=item.same_figure,
                                     use_loop=item.use_loop, cohere=item.cohere)
            plan = choreography.compose(music, pace="medium", max_seconds=SECONDS,
                                        rhythm_map=figures)
            clips = plan.template.clips
            switches = sum(1 for a, b in zip(clips, clips[1:])
                           if a.reveal_kind != b.reveal_kind)
            rate = switches / max(1, len(clips) - 1)
            rates[name] = (rate, len(figures.patterns))
            bar = music.beat_duration * music.meter
            say(f"   {name:12s} {music.bpm:6.1f} {music.meter:5d} {bar:6.2f} "
                f"{len(figures.patterns):7d} "
                f"{('yes' if figures.looped else 'no'):>5s} "
                f"{len(clips):6d} {rate:6.0%}")
            if music.preset != name:
                say(f"   [FAIL] {name} did not record itself on the analysis")
                problems += 1
            seen.setdefault((round(music.bpm, 1), music.meter,
                             len(figures.patterns), len(clips)), []).append(name)

        # The point of presets is choice. If they collapse onto one reading there is no
        # choice, only the illusion of one.
        distinct = len(seen)
        say(f"\n   {distinct} distinct readings out of {len(analysis.PRESETS)}")
        for key, names in seen.items():
            if len(names) > 1:
                say(f"   note: {', '.join(names)} agree ({key[0]} BPM, {key[1]}/4, "
                    f"{key[2]} figures, {key[3]} photos)")
        if distinct < 4:
            say("   [FAIL] the presets barely differ, so the choice is not real")
            problems += 1
        else:
            say("   [PASS] the presets genuinely disagree")

        # The complaint this whole thing exists to answer: a repeating track showing a
        # different animation every few seconds. Any reading that claims to cohere has to
        # hold one entrance; only fine-detail is allowed to be restless, and it says so.
        say("")
        held = 0
        for name, item in analysis.PRESETS.items():
            if name not in rates:
                continue
            rate, count = rates[name]
            if item.cohere and rate > 0.20:
                say(f"   [FAIL] {name} claims to cohere but changed arrival on "
                    f"{rate:.0%} of cuts")
                problems += 1
            elif item.cohere:
                held += 1
            # A track read as one figure has nothing to vary, so the varied reading is only
            # held to its promise when the bars actually fell into more than one group.
            elif count > 1 and rate < 0.10:
                say(f"   [FAIL] {name} is meant to be varied but changed arrival on "
                    f"only {rate:.0%} of cuts across {count} figures")
                problems += 1
        say(f"   [PASS] {held} cohering reading(s) held one entrance throughout")

        # A look that permits four arrivals and then shows a fifth is worse than no look at
        # all, because the restriction is the entire promise. Every clip is checked, not a
        # sample, and the softened forms count too.
        say("")
        say(f"4. every look stays inside its own vocabulary on {track.name}")
        say(f"   {'look':10s} {'arrivals used':44s} {'shape':7s} {'transitions':22s}")
        music = analysis.analyse(track, duration=SECONDS, fps=30.0)
        for name, item in effects.LOOKS.items():
            figures = rhythm.analyse(music, look=name)
            plan = choreography.compose(music, pace="medium", max_seconds=SECONDS,
                                        rhythm_map=figures)
            clips = plan.template.clips
            if item.shape:
                for clip in clips:
                    clip.reveal_shape = item.shape
            if item.transitions:
                for clip in clips:
                    if clip.transition not in item.transitions:
                        clip.transition = item.transitions[0]
            used = sorted({c.reveal_kind for c in clips})
            moves = sorted({c.transition for c in clips})
            shapes = sorted({c.reveal_shape for c in clips if c.reveal_total > 0})
            say(f"   {name:10s} {', '.join(used)[:44]:44s} "
                f"{(','.join(shapes) or '-'):7s} {', '.join(moves)[:22]:22s}")

            # "cut" is always allowed: a photo too short to build anything has to appear
            # somehow, and appearing instantly is the honest answer.
            if item.reveals:
                stray = [u for u in used if u not in item.reveals and u != "cut"]
                if stray:
                    say(f"   [FAIL] {name} used {', '.join(stray)}, which it does not allow")
                    problems += 1
            if item.shape and any(s != item.shape for s in shapes):
                say(f"   [FAIL] {name} drew pieces as {shapes}, not {item.shape}")
                problems += 1
            if item.transitions:
                off = [m for m in moves if m not in item.transitions]
                if off:
                    say(f"   [FAIL] {name} used transition(s) {', '.join(off)}")
                    problems += 1
        say("   [PASS] no look showed an arrival, shape or transition it does not permit")

    say("")
    say("ALL CHECKS PASSED" if not problems else f"{problems} PROBLEM(S)")
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(3)
