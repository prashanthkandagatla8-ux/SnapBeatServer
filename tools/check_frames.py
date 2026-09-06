"""Check every frame shape renders, including 16:9.

A template records a portrait canvas, but any style can be rendered wide or square because
positions are held in half-canvas units and horizontal travel is corrected by the aspect
ratio. This confirms that holds in practice rather than in principle: the output really is
the requested shape, and no shape leaves the frame uncovered.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import beats, config, photos as photo_mod, renderer  # noqa: E402
from beatcanvas.template import Template  # noqa: E402

MUSIC = Path(r"C:\Users\prash\Music\Little Do You Know Beat Cry.mp4")
CARDS = config.ROOT / "_placeholders"
REPORT = config.ROOT / "_logs" / "frames_report.txt"

# One style per kind of motion, so a shape problem shows up wherever it lives.
STYLES = ("beat-pendulum.json", "glide-pan.json", "reveal-circles.json",
          "punch-cut.json")

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


def probe_size(path: Path) -> tuple[int, int]:
    """Ask ffprobe what shape the finished file actually is."""
    result = subprocess.run(
        [config.FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True)
    parts = result.stdout.strip().split("x")
    if len(parts) != 2:
        return (0, 0)
    return int(parts[0]), int(parts[1])


def main() -> int:
    analysis = beats.analyse(MUSIC, duration=12.0)
    strong = analysis.strong_times()
    cards = photo_mod.collect(CARDS)
    say(f"{len(cards)} cards, {len(analysis.onsets)} beats, "
        f"{len(strong)} main\n")

    check("three frame shapes are offered", len(config.FRAME_SIZES) == 3,
          ", ".join(config.FRAME_SIZES))
    check("16:9 is one of them", config.FRAME_SIZES.get("landscape") == (1920, 1080),
          str(config.FRAME_SIZES.get("landscape")))

    for shape, (width, height) in config.FRAME_SIZES.items():
        say(f"\n{shape} {width}x{height}")
        for style in STYLES:
            template = Template.load(config.TEMPLATE_DIR / style)
            template.width, template.height = width, height
            built = template.with_beats(analysis.onsets, analysis.duration,
                                        strong=strong)
            built = built.adapt(len(cards), "repeat")
            built.clips = built.clips[:6]
            check(f"{built.name}: aspect follows the request",
                  abs(built.aspect - width / height) < 1e-6,
                  f"{built.aspect:.3f}")

            # A quarter-size render keeps this quick; coverage is a proportion.
            probe = Template.from_dict(built.to_dict())
            probe.width, probe.height = width // 4, height // 4
            photo_set = photo_mod.PhotoSet(
                cards, (probe.width, probe.height),
                renderer.required_headroom(probe))

            reveals = probe.reveal_tiles > 0
            worst = 0.0
            for clip in probe.clips:
                if reveals and clip.reveal_shown < clip.reveal_total:
                    continue
                for fraction in (0.0, 0.1, 0.5, 0.9):
                    frame = renderer.render_frame(
                        probe, photo_set, clip.start + clip.duration * fraction)
                    worst = max(worst, float((frame.max(axis=2) < 8).mean()))
            check(f"{built.name}: fills the frame", worst < 0.005,
                  f"{worst * 100:.2f}% black")

    # One real encode per shape, to be sure the file itself is the right shape.
    say("\nencoded output really is the requested shape")
    template = Template.load(config.TEMPLATE_DIR / "beat-pulse.json")
    for shape, (width, height) in config.FRAME_SIZES.items():
        sized = Template.load(config.TEMPLATE_DIR / "beat-pulse.json")
        sized.width, sized.height = width, height
        built = sized.with_beats(analysis.onsets, analysis.duration, strong=strong)
        built = built.adapt(len(cards), "repeat")
        built.clips = built.clips[:8]
        built.audio_path = str(MUSIC)
        photo_set = photo_mod.PhotoSet(cards, (built.width, built.height),
                                       renderer.required_headroom(built))
        out = config.OUTPUT_DIR / f"FRAME_{shape}.mp4"
        result = renderer.render(built, photo_set, out, scale=0.5)
        actual = probe_size(out) if out.exists() else (0, 0)
        check(f"{shape}: encoded at the right ratio",
              actual[0] > 0 and abs(actual[0] / actual[1] - width / height) < 0.02,
              f"{actual[0]}x{actual[1]} from {width}x{height}, "
              f"{result.frames} frames")
    del template

    say("\n" + "=" * 60)
    if failures:
        say(f"{failures} check(s) FAILED")
        return 1
    say("every frame shape works, 16:9 included")
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
