"""Prove the music-agnostic templates work on a track they have never seen.

The fixed templates were verified against the CapCut edit they came from. These carry no
timing at all, so what needs proving is different: that cuts get generated from an
arbitrary song, land on its beats, and render.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import beats, config, photos as photo_mod, renderer  # noqa: E402
from beatcanvas.template import MIN_PHOTOS, Template  # noqa: E402

MUSIC = Path(r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4")
CARDS = config.ROOT / "_placeholders"
REPORT = config.ROOT / "_logs" / "any_music_report.txt"

failures: list[str] = []
_lines: list[str] = []
_current = ""


def say(text: str = "") -> None:
    """Print and also write the report, because console output here is unreliable."""
    print(text)
    _lines.append(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(_lines) + "\n", encoding="utf-8")


def check(label: str, ok: bool, detail: str = "") -> None:
    say(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        # Record which template it was, or a repeated label says nothing about where.
        failures.append(f"{_current}: {label}" if _current else label)


def main() -> int:
    if not MUSIC.exists():
        print(f"music not found: {MUSIC}")
        return 2
    cards = photo_mod.collect(CARDS)
    if len(cards) < MIN_PHOTOS:
        print("no placeholder cards; run tools\\make_placeholders.py first")
        return 2

    say(f"music : {MUSIC.name}")
    analysis = beats.analyse(MUSIC, duration=36.0)
    say(f"        {len(analysis.onsets)} onsets, {analysis.tempo_bpm:.1f} BPM, "
        f"{analysis.duration:.1f}s analysed\n")

    flexible = []
    for path in sorted(config.TEMPLATE_DIR.glob("*.json")):
        template = Template.load(path)
        if template.music_mode == "any":
            flexible.append((path, template))

    check("found any-music templates", len(flexible) >= 5, f"{len(flexible)}")

    global _current
    strong = analysis.strong_times()
    for path, template in flexible:
        _current = template.name
        say(f"\n{template.name}  ({path.name})")
        check("ships with no baked timing", len(template.clips) == 0)
        check("ships with no bundled music", not template.audio_path)

        # The main beats have to be supplied, or a reveal style has nothing to group by
        # and quietly falls back to behaving like a plain cut.
        built = template.with_beats(analysis.onsets, analysis.duration, strong=strong)
        check("cuts were generated", len(built.clips) >= 4,
              f"{len(built.clips)} clips over {built.duration:.1f}s")
        check("respects its shortest-clip setting",
              all(c.duration >= template.min_clip_duration * 0.5
                  for c in built.clips),
              f"min {min((c.duration for c in built.clips), default=0):.3f}s")
        check("stays within the requested length",
              built.duration <= (template.max_seconds or 1e9) + 1.5,
              f"{built.duration:.1f}s vs cap {template.max_seconds:g}s")
        check("clips are contiguous with no gaps",
              all(abs(built.clips[i].end - built.clips[i + 1].start) < 1e-3
                  for i in range(len(built.clips) - 1)))

        # Every generated cut should sit on a detected onset.
        off = 0
        for clip in built.clips[1:]:
            nearest = min(analysis.onsets, key=lambda t: abs(t - clip.start))
            if abs(clip.start - nearest) > 0.03:
                off += 1
        check("cuts land on detected beats", off == 0,
              f"{off} of {len(built.clips) - 1} off the beat")

        # Render a couple of seconds to confirm the style actually draws.
        short = Template.from_dict(built.to_dict())
        short.clips = short.clips[:6]
        short.audio_path = str(MUSIC)
        scale = 0.25
        width = int(round(short.width * scale))
        height = int(round(short.height * scale))
        probe = Template.from_dict(short.to_dict())
        probe.width, probe.height = width, height
        headroom = renderer.required_headroom(probe)
        photo_set = photo_mod.PhotoSet(cards, (width, height), headroom)

        out = config.OUTPUT_DIR / f"ANY_{path.stem}.mp4"
        result = renderer.render(short, photo_set, out, scale=scale)
        check("renders", out.exists() and result.frames > 0,
              f"{result.frames} frames, {result.seconds_per_frame * 1000:.0f} ms/frame")
        check("music muxed in", result.audio_muxed)

        # A reveal style shows its photo a piece at a time, so blank area is the whole
        # point there and only the finished pieces can be judged. Every other style is
        # meant to fill the frame at every instant.
        reveals = probe.reveal_tiles > 0
        worst = 0.0
        for index, clip in enumerate(probe.clips[:4]):
            if reveals and clip.reveal_shown < clip.reveal_total:
                continue
            # The opening of the very first clip is allowed to be dark: a video that fades
            # up from black at the start is doing so deliberately, because there is no
            # earlier photo to come from. A gap anywhere after that is a fault.
            fractions = (0.5, 0.95) if index == 0 else (0.0, 0.1, 0.5, 0.95)
            for fraction in fractions:
                frame = renderer.render_frame(
                    probe, photo_set, clip.start + clip.duration * fraction)
                worst = max(worst, float((frame.max(axis=2) < 8).mean()))
        check("no black gap at any point", worst < 0.005,
              f"worst {worst * 100:.2f}% near-black"
              + (" (fully revealed clips only)" if reveals else ""))

        if template.animation == "Fade" and len(probe.clips) > 2:
            # A dissolve must start from the picture it is replacing. Multiplying a lone
            # photo by its alpha only darkens the frame, which made every cut flash black.
            second = probe.clips[1]
            before = renderer.render_frame(probe, photo_set, second.start - 1e-3)
            opening = renderer.render_frame(probe, photo_set, second.start + 1e-3)
            settled = renderer.render_frame(
                probe, photo_set, second.start + second.duration * 0.9)
            drift = float(abs(opening.astype(int) - before.astype(int)).mean())
            check("a mid-video dissolve starts from the outgoing photo", drift < 12.0,
                  f"differs from the previous frame by {drift:.1f} of 255")
            check("a mid-video dissolve never darkens the frame",
                  float(opening.mean()) > float(settled.mean()) * 0.55,
                  f"opening {opening.mean():.0f} vs settled {settled.mean():.0f}")

        if reveals:
            # A reveal style has to actually reveal. Holding a photo for several main
            # beats once thinned the in-between beats away, leaving one piece per photo
            # and no reveal at all, which this catches.
            groups: dict[int, list] = {}
            for clip in built.clips:
                groups.setdefault(clip.slot, []).append(clip)
            multi = [g for g in groups.values() if len(g) > 1]
            sizes = sorted({g[0].reveal_total for g in groups.values()})
            check("photos really are revealed in pieces", len(multi) >= 2,
                  f"{len(multi)} of {len(groups)} photos span more than one beat, "
                  f"piece counts {sizes}")
            check("pieces equal the beats each photo spans",
                  all(g[0].reveal_total == len(g) for g in groups.values()),
                  f"piece counts {sizes}")

    _current = ""
    say("\n" + "=" * 60)
    if failures:
        say(f"{len(failures)} check(s) FAILED:")
        for name in failures:
            say(f"  - {name}")
        return 1
    say("all any-music checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
