"""Draw the application icon and write it as a Windows .ico.

Written by hand rather than with an image library because the only ones installed here are
numpy and OpenCV, and neither writes .ico. The format is simple enough: a small directory
followed by one PNG per size, which Windows has accepted since Vista.

    tools\\run.bat tools\\make_icon.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from beatcanvas import config  # noqa: E402

TARGET = config.ROOT / "_assets" / "beatcanvas.ico"
PREVIEW = config.ROOT / "_assets" / "beatcanvas.png"

#: Windows picks the nearest of these for the size it needs, so the small ones are drawn
#: separately rather than scaled down from one large image: a 16px icon scaled from 256px
#: turns to mud.
SIZES = (16, 24, 32, 48, 64, 128, 256)

#: Deep blue-black plate with a warm accent, so it reads on both light and dark taskbars.
BACKGROUND = (28, 20, 14)          # BGR
BAR_WARM = (86, 140, 240)
BAR_HOT = (120, 200, 255)


def draw(size: int) -> np.ndarray:
    """One icon at one size, as BGRA."""
    scale = 4 if size <= 64 else 2         # supersample, then shrink, for clean edges
    big = size * scale
    canvas = np.zeros((big, big, 4), dtype=np.uint8)

    # Rounded plate.
    radius = int(big * 0.22)
    plate = np.zeros((big, big), dtype=np.uint8)
    cv2.rectangle(plate, (radius, 0), (big - radius, big), 255, -1)
    cv2.rectangle(plate, (0, radius), (big, big - radius), 255, -1)
    for centre in ((radius, radius), (big - radius, radius),
                   (radius, big - radius), (big - radius, big - radius)):
        cv2.circle(plate, centre, radius, 255, -1)

    canvas[:, :, 0][plate > 0] = BACKGROUND[0]
    canvas[:, :, 1][plate > 0] = BACKGROUND[1]
    canvas[:, :, 2][plate > 0] = BACKGROUND[2]
    canvas[:, :, 3] = plate

    # A row of bars: the beat, which is what the whole thing is about. Heights are fixed
    # rather than random so the icon is identical every time it is built.
    heights = (0.34, 0.62, 0.92, 0.50, 0.76, 0.40)
    count = len(heights)
    margin = big * 0.17
    span = big - margin * 2
    slot = span / count
    width = slot * 0.52

    for index, height in enumerate(heights):
        left = int(round(margin + slot * index + (slot - width) / 2))
        right = int(round(left + width))
        tall = height * (big - margin * 2)
        top = int(round((big - tall) / 2))
        bottom = int(round(top + tall))
        # The taller the bar the hotter it is, so the shape reads even at 16 pixels.
        mix = height
        colour = tuple(int(BAR_WARM[c] * (1 - mix) + BAR_HOT[c] * mix) for c in range(3))
        bar_radius = max(1, int(width * 0.45))
        cv2.rectangle(canvas, (left, top + bar_radius), (right, bottom - bar_radius),
                      (*colour, 255), -1)
        cv2.circle(canvas, ((left + right) // 2, top + bar_radius), bar_radius,
                   (*colour, 255), -1)
        cv2.circle(canvas, ((left + right) // 2, bottom - bar_radius), bar_radius,
                   (*colour, 255), -1)

    small = cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA)
    # Shrinking softens the alpha edge, which leaves the plate looking smudged against a
    # light taskbar. Lifting alpha slightly and clipping restores a definite edge without
    # bringing back the jaggies supersampling removed.
    alpha = small[:, :, 3].astype(np.float32) / 255.0
    small[:, :, 3] = (np.clip(alpha * 1.25, 0.0, 1.0) * 255).astype(np.uint8)
    return small


def write_ico(images: list[np.ndarray], target: Path) -> None:
    """Pack PNG-encoded images into an .ico container."""
    payloads = []
    for image in images:
        ok, buffer = cv2.imencode(".png", image)
        if not ok:
            raise RuntimeError("could not encode an icon frame as PNG")
        payloads.append(buffer.tobytes())

    count = len(payloads)
    # Header, then one 16-byte directory entry per image, then the images themselves.
    offset = 6 + 16 * count
    header = struct.pack("<HHH", 0, 1, count)
    entries, blob = b"", b""
    for image, payload in zip(images, payloads):
        side = image.shape[0]
        entries += struct.pack(
            "<BBBBHHII",
            0 if side >= 256 else side,     # 0 means 256 in this format
            0 if side >= 256 else side,
            0, 0, 1, 32, len(payload), offset)
        offset += len(payload)
        blob += payload

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(header + entries + blob)


def main() -> int:
    images = [draw(size) for size in SIZES]
    write_ico(images, TARGET)
    cv2.imwrite(str(PREVIEW), images[-1])
    print(f"wrote {TARGET} ({TARGET.stat().st_size} bytes, "
          f"{len(SIZES)} sizes: {', '.join(str(s) for s in SIZES)})")
    print(f"wrote {PREVIEW} for a look at it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
