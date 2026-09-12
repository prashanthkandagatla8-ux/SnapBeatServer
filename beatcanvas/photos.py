"""Loading and preparing the user's pictures.

Photos arrive at any size and orientation, so each one is oriented from its EXIF tag,
scaled to cover the canvas, and cached at working resolution. Caching matters: a clip is
on screen for many frames and the same picture would otherwise be decoded and resized
every single frame.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import config


@dataclass
class PreparedPhoto:
    """A picture already oriented and sized for the canvas."""

    path: Path
    image: np.ndarray            # BGR uint8
    original_size: tuple[int, int]

    @property
    def size(self) -> tuple[int, int]:
        return self.image.shape[1], self.image.shape[0]


def collect(folder: str | Path, recursive: bool = False) -> list[Path]:
    """Image files in a folder, in a stable, human-sensible order."""
    folder = Path(folder)
    if not folder.exists():
        raise FileNotFoundError(folder)
    pattern = "**/*" if recursive else "*"
    files = [
        entry for entry in folder.glob(pattern)
        if entry.is_file() and entry.suffix.lower() in config.IMAGE_EXTENSIONS
    ]
    return sorted(files, key=_natural_key)


def _natural_key(path: Path) -> tuple:
    """Sort so 'img2' comes before 'img10'."""
    parts: list = []
    digits = ""
    for char in path.stem:
        if char.isdigit():
            digits += char
        else:
            if digits:
                parts.append((1, int(digits)))
                digits = ""
            parts.append((0, char.lower()))
    if digits:
        parts.append((1, int(digits)))
    return (tuple(parts), path.name.lower())


_EXIF_ORIENTATION = 274


def _apply_exif_orientation(image: np.ndarray, path: Path) -> np.ndarray:
    """Rotate to match the EXIF orientation tag.

    Phone photos are very often stored landscape with a rotate flag. OpenCV ignores that
    flag, so without this a portrait picture renders on its side.
    """
    try:
        from PIL import Image, ExifTags  # noqa: F401  (optional dependency)
    except Exception:
        return image

    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as handle:
            exif = handle.getexif()
            orientation = exif.get(_EXIF_ORIENTATION)
    except Exception:
        return image

    if not orientation or orientation == 1:
        return image
    if orientation == 2:
        return cv2.flip(image, 1)
    if orientation == 3:
        return cv2.rotate(image, cv2.ROTATE_180)
    if orientation == 4:
        return cv2.flip(image, 0)
    if orientation == 5:
        return cv2.rotate(cv2.flip(image, 1), cv2.ROTATE_90_CLOCKWISE)
    if orientation == 6:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if orientation == 7:
        return cv2.rotate(cv2.flip(image, 1), cv2.ROTATE_90_COUNTERCLOCKWISE)
    if orientation == 8:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image


def load(path: str | Path, canvas: tuple[int, int],
         headroom: float = 1.35) -> PreparedPhoto:
    """Load one picture, oriented and scaled to cover the canvas.

    ``headroom`` oversizes the picture beyond a bare cover fit. The animations move and
    rotate the layer, so a picture sized exactly to the canvas would expose empty corners
    as soon as it swings. Oversizing costs memory but guarantees the frame stays filled.
    """
    path = Path(path)
    data = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if data is not None:
        if data.ndim == 2:
            data = cv2.cvtColor(data, cv2.COLOR_GRAY2BGR)
        elif data.ndim == 3 and data.shape[2] == 4:
            data = cv2.cvtColor(data, cv2.COLOR_BGRA2BGR)
        elif data.ndim == 3 and data.shape[2] == 1:
            data = cv2.cvtColor(data, cv2.COLOR_GRAY2BGR)
    if data is None:
        raise OSError(f"could not read image: {path}")

    # Composite alpha onto white so transparent PNG areas don't render as black.
    if data.ndim == 3 and data.shape[2] == 4:
        alpha = data[:, :, 3:4].astype(np.float32) / 255.0
        rgb = data[:, :, :3].astype(np.float32)
        white = np.full_like(rgb, 255.0)
        data = (rgb * alpha + white * (1.0 - alpha)).astype(np.uint8)

    original = (data.shape[1], data.shape[0])
    data = _apply_exif_orientation(data, path)

    canvas_w, canvas_h = canvas
    target_w = canvas_w * headroom
    target_h = canvas_h * headroom
    scale = max(target_w / data.shape[1], target_h / data.shape[0])

    if abs(scale - 1.0) > 1e-3:
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
        new_size = (max(1, int(round(data.shape[1] * scale))),
                    max(1, int(round(data.shape[0] * scale))))
        data = cv2.resize(data, new_size, interpolation=interpolation)

    return PreparedPhoto(path=path, image=data, original_size=original)


class PhotoSet:
    """The pictures for one render, addressed by slot, loaded once each."""

    def __init__(self, paths: list[str | Path], canvas: tuple[int, int],
                 headroom: float = 1.35):
        self.paths = [Path(p) for p in paths]
        self.canvas = canvas
        self.headroom = headroom
        self._cache: dict[int, PreparedPhoto] = {}

    def __len__(self) -> int:
        return len(self.paths)

    def for_slot(self, slot: int) -> PreparedPhoto:
        """The picture for a slot, cycling if fewer pictures were supplied than slots."""
        if not self.paths:
            raise ValueError("no photos supplied")
        resolved = slot % len(self.paths)
        if resolved not in self._cache:
            self._cache[resolved] = load(self.paths[resolved], self.canvas,
                                        self.headroom)
        return self._cache[resolved]

    def preload(self, slots: list[int]) -> None:
        for slot in sorted(set(slots)):
            self.for_slot(slot)
