"""Generate numbered placeholder images.

Real photographs make it hard to see what the animation is doing: you cannot tell which
slot is on screen, which way it entered, or whether it is rotated. Flat numbered cards
make the motion legible, and they load instantly.

    python tools\\make_placeholders.py --count 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

OUT = config.ROOT / "_placeholders"

#: Distinct, reasonably dark hues so white text stays readable on every card.
PALETTE = [
    (52, 73, 94), (41, 128, 185), (39, 174, 96), (192, 57, 43),
    (142, 68, 173), (211, 84, 0), (22, 160, 133), (127, 140, 141),
    (44, 62, 80), (243, 156, 18),
]


def make_card(index: int, width: int, height: int) -> np.ndarray:
    colour = PALETTE[index % len(PALETTE)]
    card = np.zeros((height, width, 3), dtype=np.uint8)
    # Vertical gradient, so any rotation or flip is immediately obvious.
    top = np.array(colour, dtype=np.float32)
    bottom = top * 0.45
    for row in range(height):
        blend = row / max(1, height - 1)
        card[row, :] = (top * (1.0 - blend) + bottom * blend).astype(np.uint8)

    # Border helps show when the picture stops covering the frame.
    cv2.rectangle(card, (0, 0), (width - 1, height - 1), (255, 255, 255), max(2, width // 180))

    label = f"photo {index + 1}"
    scale = width / 420.0
    thickness = max(2, int(round(scale * 2.4)))
    (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    origin = ((width - text_w) // 2, (height + text_h) // 2)
    cv2.putText(card, label, origin, cv2.FONT_HERSHEY_SIMPLEX, scale,
                (0, 0, 0), thickness + 3, cv2.LINE_AA)
    cv2.putText(card, label, origin, cv2.FONT_HERSHEY_SIMPLEX, scale,
                (255, 255, 255), thickness, cv2.LINE_AA)

    # An up-arrow at the top makes orientation unambiguous.
    cx = width // 2
    top_y = int(height * 0.16)
    size = int(width * 0.06)
    arrow = np.array([[cx, top_y - size], [cx - size, top_y + size],
                      [cx + size, top_y + size]], dtype=np.int32)
    cv2.polylines(card, [arrow], True, (255, 255, 255), max(2, thickness // 2), cv2.LINE_AA)

    return card


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    folder = Path(args.out)
    folder.mkdir(parents=True, exist_ok=True)
    for existing in folder.glob("photo_*.png"):
        existing.unlink()

    for index in range(args.count):
        card = make_card(index, args.width, args.height)
        # Zero-padded so natural and lexical ordering agree.
        path = folder / f"photo_{index + 1:02d}.png"
        cv2.imwrite(str(path), card)

    print(f"wrote {args.count} placeholder(s) at {args.width}x{args.height} to {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
