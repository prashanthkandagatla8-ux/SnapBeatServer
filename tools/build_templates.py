"""Build the shipped template set.

Two kinds:

**Fixed** keeps a specific edit. The cut points and the music are both baked in, so it
reproduces that one video exactly and only works with that track. These come from the
imported CapCut draft.

**Any music** carries a style but no timing at all. Cuts are generated from the onsets of
whatever song is supplied, so one template covers every track. These are duplicates of
the fixed pair plus a few that only make sense as general-purpose styles.

    tools\\run.bat tools\\build_templates.py --fixed-source templates\\<name>.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402
from beatcanvas.template import Template  # noqa: E402

#: Music-agnostic styles. Ordered roughly from calmest to most aggressive, which is also
#: the order they appear in the interface.
FLEXIBLE = [
    {
        "file": "beat-cut.json",
        "name": "Beat Cut",
        "animation": "Cut",
        "cover_mode": "zoom",
        "min_clip_duration": 0.20,
        "note": "Photos change on the beat with no movement at all. The simplest "
                "option, and the one that never fights the picture.",
    },
    {
        "file": "beat-bounce.json",
        "name": "Beat Bounce",
        "animation": "Bounce",
        "cover_mode": "zoom",
        "min_clip_duration": 0.20,
        "note": "Each photo lands oversized and springs once before resting. Reads as "
                "playful rather than aggressive.",
    },
    {
        "file": "beat-fade.json",
        "name": "Beat Fade",
        "animation": "Fade",
        "cover_mode": "zoom",
        "beats_per_clip": 2,
        "min_clip_duration": 0.35,
        "note": "Photos cross over into one another on every second beat. The outgoing "
                "picture is held underneath, so the frame never dips to black.",
    },
    {
        "file": "beat-spin.json",
        "name": "Beat Spin",
        "animation": "Spin",
        "cover_mode": "zoom",
        "beats_per_clip": 2,
        "min_clip_duration": 0.25,
        "note": "A slow turn through each photo with a gentle zoom. Adds rotation "
                "without making the picture hard to read.",
    },
    {
        "file": "cinematic-zoom.json",
        "name": "Cinematic Zoom",
        "animation": "Zoom In",
        "cover_mode": "zoom",
        "beats_per_clip": 4,
        "min_clip_duration": 0.80,
        "note": "One long slow push into each photo, changing every fourth beat. The "
                "quietest style here.",
    },
    {
        "file": "punch-cut.json",
        "name": "Punch Cut",
        "animation": "Punch In",
        "cover_mode": "zoom",
        "min_clip_duration": 0.18,
        "note": "A hard scale snap that hits exactly on the beat and then holds still. "
                "Made for sharp drums; the stillness between hits is what sells it.",
    },
    {
        "file": "zoom-out-reveal.json",
        "name": "Zoom Out",
        "animation": "Zoom Out",
        "cover_mode": "zoom",
        "beats_per_clip": 2,
        "min_clip_duration": 0.40,
        "note": "Each photo opens wide and eases back into the frame, which pulls the "
                "eye inwards. Calmer than a punch but still directed.",
    },
    {
        "file": "sway-ballad.json",
        "name": "Sway",
        "animation": "Sway",
        "cover_mode": "zoom",
        "beats_per_clip": 4,
        "min_clip_duration": 0.90,
        "note": "A slow lean one way and back across each photo, changing every fourth "
                "beat. Built for ballads, where anything faster feels rushed.",
    },
    {
        "file": "glide-pan.json",
        "name": "Glide",
        "animation": "Glide",
        "cover_mode": "mirror",
        "beats_per_clip": 2,
        "min_clip_duration": 0.45,
        "note": "The camera never stops: each photo travels steadily across the frame "
                "and cuts while still moving, so the motion carries between photos.",
    },
    {
        "file": "beat-pulse.json",
        "name": "Beat Pulse",
        "animation": "Pulse",
        "cover_mode": "zoom",
        "min_clip_duration": 0.20,
        "note": "Each photo snaps in slightly oversized and settles. Lands on the "
                "transient, so it reads on almost any track.",
    },
    {
        "file": "beat-pendulum.json",
        "name": "Beat Pendulum",
        "animation": "Pendulum 1",
        "cover_mode": "zoom",
        "min_clip_duration": 0.24,
        "note": "The CapCut pendulum bounce, cut to whatever song you give it.",
    },
    {
        "file": "beat-slide.json",
        "name": "Beat Slide",
        "animation": "Slide",
        "cover_mode": "mirror",
        "min_clip_duration": 0.22,
        "note": "Photos slide in from one side and stay, like dealing cards.",
    },
    {
        "file": "beat-whip.json",
        "name": "Beat Whip",
        "animation": "Whip",
        "cover_mode": "zoom",
        "min_clip_duration": 0.26,
        "note": "Hard, fast throw with a heavy smear. Best on percussive music; too "
                "aggressive for anything gentle.",
    },
    {
        "file": "slow-drift.json",
        "name": "Slow Drift",
        "animation": "Drift",
        "cover_mode": "zoom",
        "min_clip_duration": 0.80,
        "beats_per_clip": 4,
        "note": "A slow glide and gentle zoom, changing photo every fourth beat. For "
                "slow or sparse music where fast cutting would feel frantic.",
    },
    # -- reveal styles ---------------------------------------------------------
    # reveal_tiles switches the mechanic on; it is not a piece count. How many pieces a
    # photo is cut into is decided per photo from the beats it spans, so four in-between
    # beats give four pieces and one lands on each.
    {
        "file": "reveal-tiles.json",
        "name": "Reveal Boxes",
        "animation": "Cut",
        "cover_mode": "zoom",
        "min_clip_duration": 0.14,
        "reveal_tiles": 1,
        "reveal_shape": "box",
        "note": "Each photo arrives in pieces: every quick beat in between uncovers one "
                "more box, and the next main beat swaps to a new photo. The number of "
                "boxes follows the music, so a photo held over four beats splits into "
                "four.",
    },
    {
        "file": "reveal-circles.json",
        "name": "Reveal Circles",
        "animation": "Cut",
        "cover_mode": "zoom",
        "min_clip_duration": 0.14,
        "reveal_tiles": 1,
        "reveal_shape": "circle",
        "note": "The same beat-by-beat reveal in circles instead of boxes. The final "
                "beat of each group completes the photo to full frame, so the circles "
                "build and then resolve.",
    },
    {
        "file": "reveal-tiles-bounce.json",
        "name": "Reveal Bounce",
        "animation": "Bounce",
        "cover_mode": "zoom",
        "min_clip_duration": 0.14,
        "reveal_tiles": 1,
        "reveal_shape": "box",
        "note": "Pieces land beat by beat while the whole photo springs on each one. "
                "Busier than the plain reveal; best where the fills are loud.",
    },
    {
        "file": "reveal-spiral.json",
        "name": "Reveal Spiral",
        "animation": "Pulse",
        "cover_mode": "zoom",
        "min_clip_duration": 0.14,
        "reveal_tiles": 1,
        "reveal_shape": "circle",
        "reveal_order": "spiral",
        "note": "Circles open from the middle outwards rather than at random, so each "
                "photo grows from its centre in time with the beats.",
    },
    {
        "file": "reveal-tiles-fine.json",
        "name": "Reveal Sequence",
        "animation": "Cut",
        "cover_mode": "zoom",
        "min_clip_duration": 0.12,
        "reveal_tiles": 1,
        "reveal_shape": "box",
        "reveal_order": "sequence",
        "note": "Boxes fill in reading order instead of at random, which makes the "
                "rhythm of the reveal easy to follow.",
    },
    {
        "file": "aesthetic-beat-mosaic.json",
        "name": "Mosaic Pulse",
        "animation": "Pulse",
        "cover_mode": "zoom",
        "beats_per_clip": 2,
        "min_clip_duration": 0.20,
        "reveal_tiles": 1,
        "reveal_shape": "box",
        "reveal_order": "sequence",
        "note": "A pulsing mosaic: the photo breathes on the beat while its pieces fill "
                "in around it. Changes photo every second main beat.",
    },
    {
        "file": "mosaic-flow.json",
        "name": "Mosaic Flow",
        "animation": "Bounce",
        "cover_mode": "zoom",
        "beats_per_clip": 4,
        "min_clip_duration": 0.30,
        "reveal_tiles": 1,
        "reveal_shape": "box",
        "reveal_order": "sequence",
        "note": "Slower mosaic that holds each photo across four main beats, giving the "
                "pieces room to build before the swap.",
    },
]


def make_flexible(spec: dict, width: int, height: int, fps: float) -> Template:
    return Template(
        name=spec["name"],
        width=width, height=height, fps=fps,
        clips=[],                       # deliberately empty: timing comes from the music
        music_mode="any",
        animation=spec["animation"],
        axis="-y",
        cover_mode=spec["cover_mode"],
        beats_per_clip=int(spec.get("beats_per_clip", 1)),
        reveal_tiles=int(spec.get("reveal_tiles", 0)),
        reveal_order=str(spec.get("reveal_order", "random")),
        reveal_shape=str(spec.get("reveal_shape", "box")),
        min_clip_duration=float(spec["min_clip_duration"]),
        max_seconds=30.0,
        notes=[spec["note"]],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--fps", type=float, default=30.0)
    args = parser.parse_args()

    print("fixed templates (bundled music, baked timing)")
    fixed = []
    for path in sorted(config.TEMPLATE_DIR.glob("*.json")):
        try:
            template = Template.load(path)
        except Exception:
            continue
        if template.music_mode != "fixed" or not template.clips:
            continue
        # Older files predate these fields; fill them in so the interface can describe
        # the template without special-casing its age.
        animation = next((c.animation for c in template.clips if c.animation), "Cut")
        template.animation = animation
        template.axis = template.clips[0].axis
        template.cover_mode = template.clips[0].cover_mode
        template.save(path)
        fixed.append(template)
        print(f"  {path.name:34s} {template.name}")
        print(f"  {'':34s} {len(template.clips)} clips, {template.duration:.1f}s, "
              f"{animation}, {template.cover_mode}, music: {template.audio_name}")

    if not fixed:
        print("  none found")

    print("\nany-music templates (style only, no timing)")
    for spec in FLEXIBLE:
        template = make_flexible(spec, args.width, args.height, args.fps)
        path = config.TEMPLATE_DIR / spec["file"]
        template.save(path)
        detail = (f"{template.animation}, {template.cover_mode}, "
                  f"every {template.beats_per_clip} beat(s), "
                  f"min clip {template.min_clip_duration:g}s")
        if template.reveal_tiles:
            plural = "boxes" if template.reveal_shape == "box" else "circles"
            detail += (f", reveals in {plural} "
                       f"({template.reveal_order} order), one per beat")
        print(f"  {spec['file']:34s} {template.name}")
        print(f"  {'':34s} {detail}")

    print("\ntemplates on disk:")
    for path in sorted(config.TEMPLATE_DIR.glob("*.json")):
        template = Template.load(path)
        kind = "any music" if template.music_mode == "any" else "fixed song"
        print(f"  [{kind:9s}] {template.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
