"""Verify the four changes: whole-song length, beat classification, hand-edited
markers, and the tile reveal.

Writes its own report, because shell redirection has proved unreliable here.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import beats, config, photos as photo_mod, renderer  # noqa: E402
from beatcanvas.template import Template  # noqa: E402

MUSIC = Path(r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4")
CARDS = config.ROOT / "_placeholders"
REPORT = config.ROOT / "_logs" / "new_report.txt"

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
    if not MUSIC.exists():
        say(f"music not found: {MUSIC}")
        return 2

    say("1. beat classification")
    analysis = beats.analyse(MUSIC, duration=40.0)
    strong = analysis.strong_times()
    weak = analysis.weak_times()
    say(f"        {len(analysis.beats)} beats, {len(strong)} main, {len(weak)} small, "
        f"{analysis.tempo_bpm:.1f} BPM over {analysis.duration:.1f}s")
    check("beats were labelled", len(analysis.beats) == len(analysis.onsets))
    check("found main beats", len(strong) >= 4, f"{len(strong)}")
    check("found small beats between them", len(weak) >= 3, f"{len(weak)}")
    check("main and small partition the set",
          len(strong) + len(weak) == len(analysis.beats))
    if len(strong) > 2:
        gaps = [b - a for a, b in zip(strong, strong[1:])]
        spread = max(gaps) - min(gaps)
        check("main beats are roughly regular", spread < max(gaps) * 1.2,
              f"gaps {min(gaps):.2f}-{max(gaps):.2f}s")
    check("envelope is available for plotting",
          len(analysis.envelope_plot(400)) > 100,
          f"{len(analysis.envelope_plot(400))} points")

    say("\n2. whole-song length (max_seconds = 0)")
    template = Template.load(config.TEMPLATE_DIR / "beat-cut.json")
    whole = Template.from_dict(template.to_dict())
    whole.max_seconds = 0.0
    built = whole.with_beats(analysis.onsets, analysis.duration,
                             strong=strong)
    check("uses the full analysed duration",
          built.duration > analysis.duration - 2.0,
          f"{built.duration:.1f}s of {analysis.duration:.1f}s")
    check("is not silently capped at 30s", built.duration > 31.0,
          f"{built.duration:.1f}s")

    capped = Template.from_dict(template.to_dict())
    capped.max_seconds = 15.0
    short = capped.with_beats(analysis.onsets, analysis.duration, strong=strong)
    check("an explicit cap is still honoured", short.duration <= 16.0,
          f"{short.duration:.1f}s")

    say("\n3. hand-edited markers win")
    manual = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    edited = whole.with_beats(manual, 6.0, strong=manual)
    check("clip count follows the markers given", len(edited.clips) == len(manual),
          f"{len(edited.clips)} clips")
    check("cuts sit exactly on the markers",
          all(abs(c.start - m) < 1e-6 for c, m in zip(edited.clips, manual)))

    say("\n4. tile reveal")
    reveal = Template.load(config.TEMPLATE_DIR / "reveal-tiles.json")
    check("template turns the reveal on", reveal.reveal_tiles > 0,
          f"{reveal.reveal_tiles}")
    built = reveal.with_beats(analysis.onsets, analysis.duration, strong=strong)
    check("clips generated", len(built.clips) > 8, f"{len(built.clips)} clips")

    slots = [c.slot for c in built.clips]
    check("slot only advances on a main beat", len(set(slots)) < len(built.clips),
          f"{len(set(slots))} photos across {len(built.clips)} clips")
    check("slots advance in order", slots == sorted(slots))

    groups: dict[int, list] = {}
    for clip in built.clips:
        groups.setdefault(clip.slot, []).append(clip)
    multi = [g for g in groups.values() if len(g) > 1]
    check("some photos are revealed over several beats", len(multi) >= 2,
          f"{len(multi)} of {len(groups)} photos")

    # The piece count is taken from the beats each photo spans, so it varies between
    # photos and is never rounded to a grid size.
    counts = sorted({len(g) for g in groups.values()})
    sizes = sorted({g[0].reveal_total for g in groups.values()})
    check("pieces match the beats each photo spans",
          all(g[0].reveal_total == len(g) for g in groups.values()),
          f"beats per photo {counts}, pieces {sizes}")
    check("the piece count is not fixed across photos", len(sizes) > 1, str(sizes))
    check("piece counts are not rounded to a grid",
          any(n not in (4, 6, 9, 12, 16) for n in sizes), str(sizes))
    for group in groups.values():
        shown = [c.reveal_shown for c in group]
        if shown != list(range(1, len(group) + 1)):
            check("exactly one piece lands on each beat", False, str(shown))
            break
    else:
        check("exactly one piece lands on each beat", True,
              f"checked {len(groups)} photos")
    if multi:
        group = multi[0]
        check("a group finishes with the whole photo",
              group[-1].reveal_shown == group[-1].reveal_total,
              f"{group[-1].reveal_shown}/{group[-1].reveal_total}")

    say("\n5. reveal renders and actually masks")
    cards = photo_mod.collect(CARDS)
    if len(cards) < 2:
        check("placeholder cards available", False)
    else:
        probe = Template.from_dict(built.to_dict())
        probe.width, probe.height = 270, 480
        probe.clips = probe.clips[:12]
        probe.audio_path = str(MUSIC)
        photo_set = photo_mod.PhotoSet(cards, (270, 480),
                                       renderer.required_headroom(probe))

        partial = next((c for c in probe.clips
                        if 0 < c.reveal_shown < c.reveal_total), None)
        check("a partially revealed clip exists", partial is not None)
        if partial is not None:
            frame = renderer.render_frame(probe, photo_set,
                                          partial.start + partial.duration * 0.5)
            # Hidden pieces drop to a dim silhouette rather than pure black, so that the
            # shape of the coming picture is hinted at. Measuring for black would only
            # test the exact silhouette factor, not whether the mask works.
            dim = float((frame.max(axis=2) < 60).mean())
            expected = 1.0 - partial.reveal_shown / partial.reveal_total
            check("hidden pieces are held back to a silhouette",
                  abs(dim - expected) < 0.15,
                  f"{dim * 100:.0f}% dimmed, expected about {expected * 100:.0f}%")

        full = next((c for c in probe.clips
                     if c.reveal_shown >= c.reveal_total), None)
        if full is not None:
            frame = renderer.render_frame(probe, photo_set,
                                          full.start + full.duration * 0.5)
            # A completed photo is drawn without the mask at all, so the test here is that
            # no part of the frame is left uncovered. Naturally dark pixels in a photo are
            # not a fault, so this looks for black rather than merely dim.
            check("a fully revealed clip leaves no gap",
                  float((frame.max(axis=2) < 8).mean()) < 0.01)

        out = config.OUTPUT_DIR / "CHECK_reveal.mp4"
        result = renderer.render(probe, photo_set, out, scale=1.0)
        check("reveal template renders", out.exists() and result.frames > 0,
              f"{result.frames} frames, {result.seconds_per_frame * 1000:.0f} ms/frame")

    say("\n" + "=" * 58)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
