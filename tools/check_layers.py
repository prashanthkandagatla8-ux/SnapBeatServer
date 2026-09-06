"""Check the renderer really draws all three layers, and that a choreographed edit renders.

An effect that is planned but not drawn is the worst kind of bug here, because every other
check still passes: the plan looks right, the render succeeds, and the video is simply
wrong. So each arrival, each beat reaction and each transition is measured on real frames
to confirm it changes the picture, and in the direction it claims to.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from beatcanvas import (analysis, choreography, config, effects,  # noqa: E402
                       photos as photo_mod, renderer)
from beatcanvas.template import Clip, Template  # noqa: E402

MUSIC = Path(r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4")
CARDS = config.ROOT / "_placeholders"
REPORT = config.ROOT / "_logs" / "layers_report.txt"

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


WIDTH, HEIGHT = 270, 480


def _bench(reveal: str = "", transition: str = "", micro: tuple[str, ...] = (),
           movement: str = "Cut") -> tuple[Template, photo_mod.PhotoSet]:
    """Two clips of two different photos, so an arrival has something to arrive over."""
    beat = 0.8
    clips = [
        Clip(index=0, slot=0, start=0.0, duration=2.0, animation="Cut",
             animation_duration=2.0, cover_mode="zoom",
             transition=transition,
             transition_duration=0.4 if transition else 0.0),
        Clip(index=1, slot=1, start=2.0, duration=2.0, animation=movement,
             animation_duration=2.0, cover_mode="zoom",
             reveal_kind=reveal,
             reveal_duration=effects.duration_for(effects.REVEALS, reveal, beat)
             if reveal else 0.0,
             micro=list(micro),
             micro_beats=[2.4, 3.2] if micro else [],
             micro_intensity=1.0 if micro else 0.0),
    ]
    template = Template(name="bench", width=WIDTH, height=HEIGHT, fps=30.0,
                        clips=clips, music_mode="fixed", cover_mode="zoom",
                        max_seconds=4.0)
    cards = photo_mod.collect(CARDS)
    photos = photo_mod.PhotoSet(cards, (WIDTH, HEIGHT),
                                renderer.required_headroom(template))
    return template, photos


def _frame(template, photos, time_s) -> np.ndarray:
    return renderer.render_frame(template, photos, time_s)


def _differs(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def main() -> int:
    cards = photo_mod.collect(CARDS)
    if len(cards) < 2:
        say("need at least two placeholder photos")
        return 2

    say("1. every arrival changes what is on screen while it happens")
    plain, plain_photos = _bench()
    settled = _frame(plain, plain_photos, 3.9)
    for kind in effects.REVEALS:
        if kind in ("cut", "pieces"):
            continue          # cut is instant by definition; pieces are checked below
        template, photos = _bench(reveal=kind)
        early = _frame(template, photos, 2.02)
        late = _frame(template, photos, 3.9)
        moving = _differs(early, late)
        check(f"'{kind}' is visibly mid-arrival just after the cut", moving > 3.0,
              f"differs from settled by {moving:.1f} of 255")

    say("\n2. an arrival resolves to the same picture it would have shown anyway")
    for kind in ("scale_pop", "fade", "circle_wipe", "shutter", "split",
                 "diagonal_wipe", "soft_focus", "slide_in"):
        template, photos = _bench(reveal=kind)
        late = _frame(template, photos, 3.9)
        drift = _differs(late, settled)
        check(f"'{kind}' has finished by the end of the clip", drift < 2.0,
              f"still differs by {drift:.1f}")

    say("\n3. nothing leaves the frame uncovered")
    for kind in effects.REVEALS:
        template, photos = _bench(reveal=kind)
        worst = 0.0
        for time_s in (2.02, 2.1, 2.25, 2.5, 3.0, 3.9):
            frame = _frame(template, photos, time_s)
            worst = max(worst, float((frame.max(axis=2) < 8).mean()))
        # A wipe legitimately shows the previous photo, not black, so black really is a
        # fault for every kind here.
        check(f"'{kind}' never shows black", worst < 0.02,
              f"worst {worst * 100:.1f}% black")

    say("\n4. a wipe uncovers the previous photo rather than emptiness")
    for kind in ("circle_wipe", "split", "diagonal_wipe", "shutter"):
        template, photos = _bench(reveal=kind)
        outgoing = _frame(template, photos, 1.98)
        opening = _frame(template, photos, 2.02)
        # Barely into the wipe, most of the frame should still be the old photo.
        similarity = _differs(opening, outgoing)
        check(f"'{kind}' starts from the outgoing photo", similarity < 45.0,
              f"differs from it by {similarity:.1f}")

    say("\n5. each beat reaction changes the picture, but only slightly")
    for kind in effects.MICRO:
        template, photos = _bench(micro=(kind,))
        on_beat = _frame(template, photos, 2.42)
        between = _frame(template, photos, 2.95)
        moved = _differs(on_beat, between)
        check(f"'{kind}' reacts on the beat", moved > 0.05, f"{moved:.2f} of 255")
        check(f"'{kind}' is felt, not seen", moved < 30.0, f"{moved:.2f} of 255")

    say("\n6. reactions scale with the intensity asked for")
    strong_template, strong_photos = _bench(micro=("zoom_pulse",))
    weak_template, weak_photos = _bench(micro=("zoom_pulse",))
    for clip in weak_template.clips:
        clip.micro_intensity = 0.15
    strong = _differs(_frame(strong_template, strong_photos, 2.42),
                      _frame(strong_template, strong_photos, 2.95))
    weak = _differs(_frame(weak_template, weak_photos, 2.42),
                    _frame(weak_template, weak_photos, 2.95))
    check("a quiet section reacts less than a loud one", weak < strong,
          f"{weak:.2f} at 0.15 vs {strong:.2f} at 1.0")

    say("\n7. transitions are drawn across the cut")
    for kind in ("dissolve", "whip", "flash"):
        template, photos = _bench(transition=kind)
        crossing = _frame(template, photos, 2.05)
        base_template, base_photos = _bench()
        plain_crossing = _frame(base_template, base_photos, 2.05)
        moved = _differs(crossing, plain_crossing)
        check(f"'{kind}' alters the boundary", moved > 2.0, f"{moved:.1f} of 255")
    flash_template, flash_photos = _bench(transition="flash")
    lit = float(_frame(flash_template, flash_photos, 2.01).mean())
    unlit = float(_frame(*_bench(), 2.01).mean())
    check("a flash brightens rather than darkens", lit > unlit,
          f"{lit:.0f} vs {unlit:.0f}")

    say("\n8. a piece reveal lands one piece per hit")
    beat_times = [2.0, 2.2, 2.4, 2.6, 2.8]
    clips = [
        Clip(index=0, slot=0, start=0.0, duration=2.0, animation="Cut",
             animation_duration=2.0, cover_mode="zoom"),
        Clip(index=1, slot=1, start=2.0, duration=1.2, animation="Cut",
             animation_duration=1.2, cover_mode="zoom",
             reveal_kind="pieces", reveal_at=list(beat_times),
             reveal_total=len(beat_times), reveal_shown=len(beat_times),
             reveal_order="sequence"),
    ]
    template = Template(name="pieces", width=WIDTH, height=HEIGHT, fps=30.0,
                        clips=clips, music_mode="fixed", cover_mode="zoom",
                        max_seconds=3.2)
    photos = photo_mod.PhotoSet(photo_mod.collect(CARDS), (WIDTH, HEIGHT),
                                renderer.required_headroom(template))
    covered = []
    for moment in beat_times:
        frame = renderer.render_frame(template, photos, moment + 0.05)
        covered.append(1.0 - float((frame.max(axis=2) < 60).mean()))
    say("        share of frame revealed after each hit: "
        + ", ".join(f"{v:.0%}" for v in covered))
    check("each hit uncovers more of the photo",
          all(b >= a - 0.02 for a, b in zip(covered, covered[1:])),
          ", ".join(f"{v:.0%}" for v in covered))
    check("the first hit does not already show everything", covered[0] < 0.9,
          f"{covered[0]:.0%}")
    check("the last hit completes it", covered[-1] > 0.9, f"{covered[-1]:.0%}")

    say("\n9. a full choreographed edit renders")
    music = analysis.analyse(MUSIC, duration=24.0, fps=30.0)
    for pace in ("slow", "medium", "fast", "accurate"):
        plan = choreography.compose(music, pace=pace, width=WIDTH, height=HEIGHT,
                                    fps=30.0, max_seconds=12.0)
        template = plan.template
        template.audio_path = str(MUSIC)
        sized = template.adapt(len(cards), "repeat")
        photos = photo_mod.PhotoSet(cards, (WIDTH, HEIGHT),
                                    renderer.required_headroom(sized))
        out = config.OUTPUT_DIR / f"CHOREO_{pace}.mp4"
        result = renderer.render(sized, photos, out, scale=1.0)
        check(f"{pace}: renders with audio", out.exists() and result.audio_muxed,
              f"{result.frames} frames, "
              f"{result.seconds_per_frame * 1000:.0f} ms/frame, "
              f"{len(sized.clips)} photo changes")

        # The opening is allowed to be dark: with no earlier photo to cross over from, an
        # edit that begins on a fade genuinely does come up from black, and that reads as
        # an opening rather than a fault. A gap anywhere after that is a real problem, so
        # the worst moment is reported either way.
        opening = sized.clips[0].reveal_duration if sized.clips else 0.0
        worst, worst_at = 0.0, 0.0
        for index in range(0, min(result.frames, 300), 3):
            time_s = index / 30.0
            share = float((renderer.render_frame(sized, photos, time_s).max(axis=2)
                           < 8).mean())
            if time_s >= opening and share > worst:
                worst, worst_at = share, time_s
        check(f"{pace}: no black frame after the opening", worst < 0.02,
              f"worst {worst * 100:.1f}% at {worst_at:.2f}s "
              f"(opening fade lasts {opening:.2f}s)")

    say("\n" + "=" * 74)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("all three layers are drawn, and choreographed edits render")
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
