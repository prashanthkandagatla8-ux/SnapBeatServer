"""Check that no style leaves the frame black.

Two faults are guarded here. Fade used to multiply a lone picture by its alpha, which
darkened the whole frame instead of dissolving, so every cut flashed black. Bounce used to
settle at 0.97 scale, which is smaller than the frame and shows the edges. Both are
visible defects rather than timing ones, so they are measured on real frames.

Reveal styles are judged only on their finished clips: blank pieces are the mechanic.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from beatcanvas import beats, config, photos as photo_mod, renderer  # noqa: E402
from beatcanvas.template import Template  # noqa: E402

MUSIC = Path(r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4")
CARDS = config.ROOT / "_placeholders"
REPORT = config.ROOT / "_logs" / "gaps_report.txt"

failures = 0
_lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    _lines.append(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(_lines) + "\n", encoding="utf-8")


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    say(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures += 1


def main() -> int:
    analysis = beats.analyse(MUSIC, duration=14.0)
    strong = analysis.strong_times()
    cards = photo_mod.collect(CARDS)
    say(f"{len(cards)} cards, {len(analysis.onsets)} beats\n")

    worst_overall: dict[str, float] = {}
    for path in sorted(config.TEMPLATE_DIR.glob("*.json")):
        template = Template.load(path)
        if template.music_mode != "any":
            continue
        built = template.with_beats(analysis.onsets, analysis.duration, strong=strong)
        # A smaller canvas keeps this quick; coverage is a matter of proportion, not pixels.
        built.width, built.height = 270, 480
        built = built.adapt(len(cards), "repeat")
        headroom = renderer.required_headroom(built)
        photo_set = photo_mod.PhotoSet(cards, (built.width, built.height), headroom)

        reveals = built.reveal_tiles > 0
        worst = 0.0
        where = ""
        for clip in built.clips[:8]:
            if reveals and clip.reveal_shown < clip.reveal_total:
                continue
            for fraction in (0.0, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 0.95):
                time_s = clip.start + clip.duration * fraction
                frame = renderer.render_frame(built, photo_set, time_s)
                black = float((frame.max(axis=2) < 8).mean())
                if black > worst:
                    worst, where = black, f"clip at {clip.start:.2f}s, {fraction:.0%} in"
        worst_overall[built.name] = worst
        say(f"        {built.name:28s} worst {worst * 100:6.2f}%"
            + (f"   {where}" if worst > 0.005 else "")
            + ("   (reveal: finished clips only)" if reveals else ""))

    say("")
    for name, worst in sorted(worst_overall.items()):
        check(f"{name} fills the frame", worst < 0.005, f"{worst * 100:.2f}% black")

    say("\nthe two styles that were faulty, in detail")
    for name in ("Beat Fade", "Beat Bounce"):
        worst = worst_overall.get(name)
        if worst is None:
            check(f"{name} was measured", False, "not found")
            continue
        check(f"{name} no longer goes black", worst < 0.005, f"{worst * 100:.2f}%")

    say("\nthe dissolve actually mixes two photos rather than dimming one")
    fade = Template.load(config.TEMPLATE_DIR / "beat-fade.json")
    built = fade.with_beats(analysis.onsets, analysis.duration, strong=strong)
    built.width, built.height = 270, 480
    built = built.adapt(len(cards), "repeat")
    photo_set = photo_mod.PhotoSet(cards, (built.width, built.height),
                                   renderer.required_headroom(built))
    second = built.clips[1]
    early = renderer.render_frame(built, photo_set, second.start + 0.001)
    late = renderer.render_frame(built, photo_set,
                                 second.start + second.duration * 0.9)
    first_end = renderer.render_frame(built, photo_set, second.start - 0.001)
    check("the first frame of a dissolve is not dark",
          float(early.mean()) > float(late.mean()) * 0.5,
          f"start {early.mean():.1f} vs settled {late.mean():.1f}")
    check("a dissolve starts from the outgoing photo",
          float(np.abs(early.astype(np.int16)
                       - first_end.astype(np.int16)).mean()) < 12.0,
          f"difference {np.abs(early.astype(np.int16) - first_end.astype(np.int16)).mean():.1f}")

    say("\n" + "=" * 60)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("every style fills the frame")
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
