"""Produce the two shipped templates from one source template.

They share identical timing and motion. The only difference is how the frame stays
filled while a picture is displaced:

    Pendulum + Border Mirror   picture at exactly frame size, gaps filled by
                               reflecting the picture across its own edge.
                               No cropping whatsoever.

    Pendulum + Zoom            picture scales up by exactly the amount the
                               displacement requires, so it always covers.
                               No mirroring, and no crop while centred.

    tools\\run.bat tools\\make_variants.py --source templates\\0817-1_rebuild.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import animations, config, renderer  # noqa: E402
from beatcanvas.template import Template  # noqa: E402

VARIANTS = {
    "mirror": {
        "suffix": "pendulum-border-mirror",
        "label": "Pendulum + Border Mirror",
        "note": ("Picture sits at exactly frame size, so nothing is cropped. The gap the "
                 "bounce opens is filled by mirroring the picture across its own edge."),
    },
    "zoom": {
        "suffix": "pendulum-zoom",
        "label": "Pendulum + Zoom",
        "note": ("Picture zooms in by exactly as much as the bounce needs to keep the "
                 "frame filled, then settles back. Nothing is mirrored, and nothing is "
                 "cropped while it is centred."),
    },
}


def build(source: Template, mode: str, direction_axis: str) -> Template:
    spec = VARIANTS[mode]
    variant = Template.from_dict(source.to_dict())
    variant.name = spec["label"]

    for clip in variant.clips:
        clip.cover_mode = mode
        clip.axis = direction_axis
        # The old approach carried a static oversize on every clip to stop gaps
        # appearing. Both variants handle coverage themselves now, so that is dropped.
        clip.scale = 1.0
        clip.rotation = 0.0

    # Keep only the notes that still describe the template.
    variant.notes = [n for n in source.notes if "trimmed to" not in n]
    variant.notes.append(spec["note"])
    return variant


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="templates/0817-1_rebuild.json")
    parser.add_argument("--direction", default="up",
                        choices=["up", "down", "left", "right"])
    parser.add_argument("--replace", action="store_true",
                        help="delete the source template once both variants are written")
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        source_path = config.TEMPLATE_DIR / Path(args.source).name
    if not source_path.exists():
        print(f"source template not found: {args.source}")
        return 2

    axis = {"up": "-y", "down": "y", "right": "x", "left": "-x"}[args.direction]
    source = Template.load(source_path)
    print(f"source   : {source_path.name}")
    print(f"           {len(source.clips)} clips, {source.duration:.2f}s, "
          f"{source.width}x{source.height} @ {source.fps:g}fps")
    print(f"direction: {args.direction} (axis {axis})\n")

    written = []
    for mode in ("mirror", "zoom"):
        variant = build(source, mode, axis)
        path = config.TEMPLATE_DIR / f"{VARIANTS[mode]['suffix']}.json"
        variant.save(path)
        written.append(path)

        headroom = renderer.required_headroom(variant)
        peak = animations.peak_cover_scale(
            animations.get(variant.clips[0].animation),
            variant.clips[0].duration, variant.aspect,
            axis=axis, cover_mode=mode,
        )
        crop_at_rest = "none" if mode == "mirror" else "none while centred"
        print(f"{VARIANTS[mode]['label']}")
        print(f"  file            : {path.name}")
        print(f"  cover mode      : {mode}")
        print(f"  load headroom   : {headroom:.2f}x  (resolution, not framing)")
        print(f"  peak zoom       : {peak:.2f}x")
        print(f"  crop at rest    : {crop_at_rest}")
        print(f"  border fill     : "
              f"{'mirrored reflection' if mode == 'mirror' else 'not needed'}")
        print()

    if args.replace and source_path.exists():
        source_path.unlink()
        print(f"removed the source template {source_path.name}")

    print("templates now available:")
    for path in sorted(config.TEMPLATE_DIR.glob("*.json")):
        marker = "*" if path in written else " "
        print(f" {marker} {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
