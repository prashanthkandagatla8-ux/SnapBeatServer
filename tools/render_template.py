"""Render a template with a folder of photos.

    python tools\\render_template.py --template templates\\0817-1.json ^
        --photos "C:\\Users\\prash\\Pictures\\Batch" --scale 0.5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config, photos as photo_mod, renderer  # noqa: E402
from beatcanvas.template import (  # noqa: E402
    FILL_MODES, MIN_PHOTOS, Template, list_templates,
)

import cv2  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", default="")
    parser.add_argument("--photos", default=r"C:\Users\prash\Pictures\Batch")
    parser.add_argument("--out", default="")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="0.5 renders at half size, for a quick look")
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--sheet", action="store_true",
                        help="also write a contact sheet of frames")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--placeholders", action="store_true",
                        help="use the generated numbered cards instead of --photos")
    parser.add_argument("--intensity", type=float, default=1.0,
                        help="scale the swing amplitude; lower values crop less")
    parser.add_argument("--fill", choices=list(FILL_MODES), default="repeat",
                        help="repeat: cycle the photos and keep full length. "
                             "trim: cut the render short so nothing repeats")
    parser.add_argument("--limit", type=int, default=0,
                        help="use only the first N photos, for testing")
    args = parser.parse_args()

    if args.placeholders:
        args.photos = str(config.ROOT / "_placeholders")

    available = list_templates()
    if not args.template:
        print("templates:")
        for entry in available:
            print(f"  {entry}")
        if not available:
            print("  none yet - run tools\\import_draft.py first")
        return 0

    template = Template.load(args.template)

    if args.intensity != 1.0:
        # Amplitude drives how far a picture travels, and therefore how much it must be
        # oversized to keep the frame covered. Turning it down trades movement for less
        # cropping, which matters when the subject is near the edge of the photo.
        for clip in template.clips:
            clip.intensity = args.intensity
        print(f"swing amplitude scaled to {args.intensity:g}x")

    for line in template.summary():
        print(line)

    pictures = photo_mod.collect(args.photos, recursive=args.recursive)
    if not pictures:
        print(f"\nno images found in {args.photos}")
        return 2
    print(f"\nphotos   : {len(pictures)} found in {args.photos}")
    for entry in pictures[:24]:
        print(f"  {entry.name}")
    if len(pictures) > 24:
        print(f"  ... and {len(pictures) - 24} more")

    if args.limit:
        pictures = pictures[:max(MIN_PHOTOS, args.limit)]
        print(f"\nlimited to {len(pictures)} photo(s)")

    if len(pictures) < MIN_PHOTOS:
        print(f"\nneed at least {MIN_PHOTOS} photos, found {len(pictures)}")
        return 2

    print(f"\nfill mode: {args.fill}")
    for mode in FILL_MODES:
        marker = "->" if mode == args.fill else "  "
        print(f"  {marker} {mode:7s} {template.plan_for(len(pictures), mode)}")

    template = template.adapt(len(pictures), args.fill)

    width = int(round(template.width * args.scale))
    height = int(round(template.height * args.scale))
    headroom = renderer.required_headroom(template)
    print(f"\nheadroom : {headroom:.2f}x  "
          f"(pictures oversized so the frame stays covered while they swing)")
    photo_set = photo_mod.PhotoSet(pictures, (width, height), headroom)

    out = Path(args.out) if args.out else (
        config.OUTPUT_DIR / f"{Path(args.template).stem}"
        f"{'' if args.scale == 1.0 else f'_{args.scale:g}x'}.mp4"
    )

    def progress(current: int, total: int, message: str) -> None:
        bar = int(28 * current / max(1, total))
        sys.stdout.write(f"\r  [{'#' * bar}{'.' * (28 - bar)}] {current}/{total}")
        sys.stdout.flush()

    print(f"\nrendering {template.frame_count} frames at {width}x{height} ...")
    result = renderer.render(
        template, photo_set, out, progress=progress,
        scale=args.scale, with_audio=not args.no_audio,
    )
    print()

    print(f"\noutput   : {result.output}")
    print(f"frames   : {result.frames}")
    print(f"elapsed  : {result.elapsed_seconds:.2f}s "
          f"({result.seconds_per_frame * 1000:.0f} ms/frame, "
          f"{result.realtime_ratio:.1f} fps)")
    print(f"audio    : {'muxed' if result.audio_muxed else 'none'}")
    projected = result.seconds_per_frame * template.frame_count / (args.scale ** 2 or 1)
    if args.scale != 1.0:
        print(f"full-size projection: ~{projected:.0f}s")
    for warning in result.warnings:
        print(f"warning  : {warning}")

    if args.sheet:
        sheet_template = Template.from_dict(template.to_dict())
        sheet_template.width, sheet_template.height = width, height
        sheet = renderer.contact_sheet(sheet_template, photo_set)
        sheet_path = out.with_name(out.stem + "_sheet.png")
        cv2.imwrite(str(sheet_path), sheet)
        print(f"sheet    : {sheet_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
