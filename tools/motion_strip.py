"""Show the travel direction by sampling frames *within* a single clip.

A contact sheet across the whole timeline cannot show direction, because consecutive
samples land on different clips. Sampling inside one clip makes the motion obvious.

    python tools\\motion_strip.py --template templates\\0817-1_rebuild.json --clip 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config, photos as photo_mod, renderer  # noqa: E402
from beatcanvas.template import Template  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--clip", type=int, default=1,
                        help="which clip to inspect (pick a long one)")
    parser.add_argument("--steps", type=int, default=7)
    parser.add_argument("--photos", default=str(config.ROOT / "_placeholders"))
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    template = Template.load(args.template)
    if not (0 <= args.clip < len(template.clips)):
        print(f"clip {args.clip} out of range 0..{len(template.clips) - 1}")
        return 2
    clip = template.clips[args.clip]

    pictures = photo_mod.collect(args.photos)
    if not pictures:
        print(f"no images in {args.photos}")
        return 2

    # Render small: this is about where the picture sits, not about detail.
    scale = 0.28
    width = int(round(template.width * scale))
    height = int(round(template.height * scale))
    small = Template.from_dict(template.to_dict())
    small.width, small.height = width, height
    headroom = renderer.required_headroom(small)
    photo_set = photo_mod.PhotoSet(pictures, (width, height), headroom)

    print(f"clip {clip.index}: start {clip.start:.3f}s duration {clip.duration:.3f}s "
          f"axis {clip.axis}  animation {clip.animation}")

    frames = []
    labels = []
    for step in range(args.steps):
        fraction = step / max(1, args.steps - 1)
        # Stay just inside the clip so the last sample does not spill into the next one.
        time_s = clip.start + min(clip.duration * 0.999, clip.duration * fraction)
        frame = renderer.render_frame(small, photo_set, time_s)
        frames.append(frame)
        labels.append(f"{fraction:.2f}")

    gap = 6
    sheet = np.zeros((height, len(frames) * (width + gap) - gap, 3), dtype=np.uint8)
    for index, (frame, label) in enumerate(zip(frames, labels)):
        x = index * (width + gap)
        sheet[:, x:x + width] = frame
        cv2.putText(sheet, label, (x + 6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(sheet, label, (x + 6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (255, 255, 255), 1, cv2.LINE_AA)

    # Report the measured vertical travel, so direction is a number not an impression.
    # Track the white markings on the card, not overall brightness: the picture covers
    # the whole frame by design, so "where is it bright" answers nothing.
    centres = []
    for frame in frames:
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        mask = grey > 200
        # Ignore the sample's own caption, drawn in the top-left corner.
        mask[:28, :90] = False
        rows = np.nonzero(mask)[0]
        centres.append(float(rows.mean()) if rows.size > 20 else float("nan"))
    print("\nwhite-marking centre row per sample (smaller = higher up the frame):")
    for label, centre in zip(labels, centres):
        print(f"  t={label}  row {centre:7.1f}")
    if len(centres) >= 2 and not np.isnan(centres[0]) and not np.isnan(centres[-1]):
        travel = centres[0] - centres[-1]
        way = "upward" if travel > 0 else "downward"
        print(f"\n  net travel: {abs(travel):.1f}px {way}")

    out = Path(args.out) if args.out else (
        config.OUTPUT_DIR / f"motion_clip{clip.index}.png")
    cv2.imwrite(str(out), sheet)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
