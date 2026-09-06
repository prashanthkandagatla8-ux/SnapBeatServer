"""Pull frames from the reference export so behaviour can be compared, not guessed.

The question this answers: when the pendulum swings a picture off-centre, does CapCut
reveal the background, or is the picture scaled up enough to keep the frame covered?
That decides whether BeatCanvas should oversize pictures or let them travel off-frame.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatcanvas import config  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

BATCH = Path(r"C:\Users\prash\Pictures\Batch")

#: The tutorial recording is the only moving reference available. "001. Little Do You
#: Know Beat Cry.mp4" turned out to be the song with static album art, not a render.
DEFAULT = BATCH / (
    "Photo Transition Tutorial to Little do you know beat cry by Yagih Mael "
    "(App CapCut).mp4"
)
OUT = Path(__file__).resolve().parent.parent / "_reference"


def probe(path: Path) -> dict:
    result = subprocess.run(
        [config.FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,nb_frames,duration",
         "-of", "default=nw=1", str(path)],
        capture_output=True, text=True,
    )
    info = {}
    for line in result.stdout.strip().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            info[key] = value
    return info


def grab(path: Path, times: list[float]) -> list[np.ndarray]:
    frames = []
    for time_s in times:
        result = subprocess.run(
            [config.FFMPEG, "-v", "error", "-ss", f"{time_s:.3f}", "-i", str(path),
             "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True,
        )
        if result.returncode != 0 or not result.stdout:
            continue
        buffer = np.frombuffer(result.stdout, dtype=np.uint8)
        image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if image is not None:
            frames.append(image)
    return frames


def edge_report(image: np.ndarray, label: str) -> None:
    """How much of the frame border is near-black, i.e. uncovered background."""
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = grey.shape
    band = max(2, h // 100)
    top = grey[:band].mean()
    bottom = grey[-band:].mean()
    left = grey[:, :band].mean()
    right = grey[:, -band:].mean()
    dark = float((grey < 12).mean())
    print(f"  {label:8s} edges top={top:6.1f} bottom={bottom:6.1f} "
          f"left={left:6.1f} right={right:6.1f}   frame {dark * 100:5.2f}% near-black")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", default=str(DEFAULT))
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()

    path = Path(args.video)
    if not path.exists():
        print(f"not found: {path}")
        return 2

    info = probe(path)
    print(f"reference : {path.name}")
    for key, value in info.items():
        print(f"  {key:12s} {value}")

    duration = float(info.get("duration") or 0) or 12.0
    # Sample densely across the first few clips, where the swings happen.
    times = [round(duration * i / (args.count - 1), 3) for i in range(args.count)]
    frames = grab(path, times)
    if not frames:
        print("could not decode any frames")
        return 3

    OUT.mkdir(parents=True, exist_ok=True)
    print(f"\nsampled {len(frames)} frames at {times}")
    print("\nborder coverage (near-black border means the picture moved off frame):")
    for time_s, frame in zip(times, frames):
        edge_report(frame, f"{time_s:.2f}s")
        cv2.imwrite(str(OUT / f"ref_{time_s:07.3f}.png"), frame)

    # Tile them so the motion is visible at a glance.
    columns = 5
    rows = (len(frames) + columns - 1) // columns
    cell_w = 1600 // columns
    cell_h = int(round(cell_w * frames[0].shape[0] / frames[0].shape[1]))
    sheet = np.zeros((rows * cell_h, columns * cell_w, 3), dtype=np.uint8)
    for position, (time_s, frame) in enumerate(zip(times, frames)):
        thumb = cv2.resize(frame, (cell_w, cell_h), interpolation=cv2.INTER_AREA)
        row, column = divmod(position, columns)
        sheet[row * cell_h:(row + 1) * cell_h,
              column * cell_w:(column + 1) * cell_w] = thumb
        origin = (column * cell_w + 8, row * cell_h + 24)
        cv2.putText(sheet, f"{time_s:.2f}s", origin, cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(sheet, f"{time_s:.2f}s", origin, cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(OUT / "reference_sheet.png"), sheet)
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
