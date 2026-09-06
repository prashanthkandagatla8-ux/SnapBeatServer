"""Frame rendering and muxing.

Each output frame is one picture placed under an affine transform, optionally smeared by
a directional blur, composited onto the canvas. That is cheap enough to do on the CPU:
a 1080p frame costs tens of milliseconds, so a twelve-second video renders in seconds
rather than the minutes a browser-based renderer would need on a two-core machine.

Frames are piped straight into ffmpeg, so no intermediate image sequence is written
unless it is asked for.
"""
from __future__ import annotations

import math
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from . import animations, config
from .photos import PhotoSet
from .template import Clip, Template  # noqa: F401  (Clip used in type hints)

ProgressFn = Callable[[int, int, str], None]


@dataclass
class RenderResult:
    output: Path
    frames: int
    elapsed_seconds: float
    seconds_per_frame: float
    audio_muxed: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def realtime_ratio(self) -> float:
        return self.frames / max(1e-6, self.elapsed_seconds)


#: Line kernels are rebuilt rarely but used every frame, so they are cached by
#: (length, quantised direction).
_KERNEL_CACHE: dict[tuple[int, int, int], np.ndarray] = {}


def _line_kernel(size: int, dx: float, dy: float) -> np.ndarray:
    key = (size, int(round(dx * 64)), int(round(dy * 64)))
    cached = _KERNEL_CACHE.get(key)
    if cached is not None:
        return cached

    kernel = np.zeros((size, size), dtype=np.float32)
    centre = size // 2
    for step in range(size):
        offset = step - centre
        x = int(round(centre + dx * offset))
        y = int(round(centre + dy * offset))
        if 0 <= x < size and 0 <= y < size:
            kernel[y, x] = 1.0
    total = kernel.sum()
    if total > 0:
        kernel /= total
    _KERNEL_CACHE[key] = kernel
    return kernel


def _directional_blur(image: np.ndarray, pixels: float,
                      direction: tuple[float, float]) -> np.ndarray:
    """Smear along a direction, approximating CapCut's motion blur.

    A line kernel rather than a disc is what makes fast movement read as travel instead
    of softness. Axis-aligned smears, which is what the pendulum animations produce, take
    a separable box filter instead of a full 2D convolution: same result, and fast enough
    that the blur stops being the bottleneck.
    """
    length = int(round(min(abs(pixels), 80.0)))
    if length < 2:
        return image

    dx, dy = direction
    magnitude = math.hypot(dx, dy)
    if magnitude < 1e-6:
        return image
    dx, dy = dx / magnitude, dy / magnitude

    size = length if length % 2 == 1 else length + 1

    if abs(dy) < 0.02:                      # horizontal
        return cv2.blur(image, (size, 1), borderType=cv2.BORDER_REPLICATE)
    if abs(dx) < 0.02:                      # vertical
        return cv2.blur(image, (1, size), borderType=cv2.BORDER_REPLICATE)

    return cv2.filter2D(image, -1, _line_kernel(size, dx, dy),
                        borderType=cv2.BORDER_REPLICATE)


#: Tile layouts by count, chosen to stay close to the frame's own proportions so the
#: rectangles look deliberate rather than like leftover strips.
_TILE_GRIDS = {
    2: (1, 2), 3: (1, 3), 4: (2, 2), 6: (2, 3), 8: (2, 4),
    9: (3, 3), 12: (3, 4), 16: (4, 4), 20: (4, 5), 24: (4, 6),
}


def _tile_grid(count: int, aspect: float = 0.5625) -> tuple[int, int]:
    """Split ``count`` pieces into columns and rows that suit the frame shape.

    The piece count comes from the music, so it can be any number at all, including
    primes. Rather than force it onto a fixed grid, the split is chosen to keep each piece
    as close to square as the frame allows: a tall 9:16 frame gets more rows than columns
    and a wide 16:9 frame the reverse. Any leftover cells sit on the last row, which reads
    as a deliberate layout rather than stray slivers.
    """
    count = max(1, int(count))
    if count in _TILE_GRIDS and abs(aspect - 0.5625) < 1e-6:
        return _TILE_GRIDS[count]
    # A piece is square when columns/rows matches the frame's own width/height ratio.
    columns = max(1, int(round(math.sqrt(count * max(aspect, 1e-6)))))
    # Prefer a split with no empty cells when one is available nearby, so the grid looks
    # complete instead of gap-toothed.
    for candidate in sorted(range(max(1, columns - 2), columns + 3),
                           key=lambda c: abs(c - columns)):
        if candidate >= 1 and count % candidate == 0:
            return candidate, count // candidate
    return columns, int(math.ceil(count / columns))


def _tile_order(count: int, seed: int, order: str = "sequence",
                aspect: float = 0.5625) -> list[int]:
    if order == "sequence":
        return list(range(count))
    if order == "spiral":
        columns, rows = _tile_grid(count, aspect)
        cx, cy = (columns - 1) / 2.0, (rows - 1) / 2.0
        coords = [(divmod(i, columns), i) for i in range(count)]
        coords.sort(key=lambda item: ((item[0][1] - cx)**2 + (item[0][0] - cy)**2, item[1]))
        return [i for _, i in coords]
    # Seeded so a given picture always reveals in the same order, which keeps a re-render
    # identical instead of shuffling differently each time.
    rng = np.random.default_rng(1000 + seed)
    indices = np.arange(count)
    rng.shuffle(indices)
    return [int(i) for i in indices]


def pieces_shown(clip: Clip, time_s: float) -> int:
    """How many pieces of this photo have landed by ``time_s``.

    A template built from one clip per step carries the count directly. A choreographed
    clip instead carries the times its pieces land on, because the whole photo lives in a
    single clip there, so the count has to be read off the clock. Without this the clip
    would report itself complete from its first frame and no reveal would be visible.
    """
    if clip.reveal_at:
        return sum(1 for moment in clip.reveal_at if time_s >= moment)
    return int(clip.reveal_shown)


def _apply_reveal(canvas: np.ndarray, clip: Clip, progress: float = 0.0,
                  shown_override: int | None = None) -> np.ndarray:
    """Show only the pieces of this picture that have been revealed so far.

    The piece count comes from the clip, which took it from the beats this picture spans,
    so this draws whatever number it is given rather than assuming a grid size. Pieces are
    boxes or circles depending on the style.

    Areas not yet revealed drop to a 14% silhouette rather than pure black, so the shape of
    the coming picture is hinted at. A thin seam separates the pieces, and the piece that
    lands on the current beat gets a brief lift to mark it.
    """
    height, width = canvas.shape[:2]
    total = max(2, int(clip.reveal_total))
    aspect = width / max(1, height)
    columns, rows = _tile_grid(total, aspect)
    order_mode = getattr(clip, "reveal_order", "sequence") or "sequence"
    order = _tile_order(total, clip.reveal_seed, order=order_mode, aspect=aspect)
    shape = (getattr(clip, "reveal_shape", "box") or "box").lower()

    stepped = clip.reveal_shown if shown_override is None else shown_override
    if stepped > 0:
        # Stepped reveal from strong/weak beat groups
        shown = max(0, min(total, int(stepped)))
        new_idx = order[shown - 1] if shown > 0 else -1
        new_tile_flash = float((1.0 - progress / 0.35) ** 2) if progress < 0.35 else 0.0
    else:
        # Smooth continuous intra-clip progressive reveal across phrase duration (matching preview)
        step_phase = progress * (total + 0.5)
        shown = min(total, max(1, int(math.floor(step_phase))))
        new_idx = order[shown - 1] if shown > 0 else -1
        tile_sub = step_phase % 1.0
        new_tile_flash = float((1.0 - tile_sub / 0.45) ** 2) if tile_sub < 0.45 else 0.0

    if shown >= total and new_tile_flash <= 0.01:
        return canvas

    visible = set(order[:shown])

    # Base mask for revealed regions, with a hairline seam so the pieces read as separate
    # panels rather than merging into one another.
    mask = np.zeros((height, width), dtype=np.uint8)
    seam = max(1, int(round(min(width, height) * 0.0025)))

    for index in visible:
        row, column = divmod(index, columns)
        y0 = int(round(row * height / rows))
        y1 = int(round((row + 1) * height / rows))
        x0 = int(round(column * width / columns))
        x1 = int(round((column + 1) * width / columns))
        y0_in = min(y1 - 1, y0 + seam)
        y1_in = max(y0_in + 1, y1 - seam)
        x0_in = min(x1 - 1, x0 + seam)
        x1_in = max(x0_in + 1, x1 - seam)
        if shape == "circle":
            # An ellipse inscribed in the cell, so circles tile the frame evenly and
            # still cover it as the last pieces land.
            centre = ((x0_in + x1_in) // 2, (y0_in + y1_in) // 2)
            axes = (max(1, (x1_in - x0_in) // 2), max(1, (y1_in - y0_in) // 2))
            cv2.ellipse(mask, centre, axes, 0.0, 0.0, 360.0, 1, thickness=-1)
        else:
            mask[y0_in:y1_in, x0_in:x1_in] = 1

    mask = mask.astype(bool)

    # Atmospheric dark silhouette for unrevealed areas
    canvas[~mask] = (canvas[~mask].astype(np.float32) * 0.14).astype(np.uint8)

    # A brief lift on the piece that just landed, so the eye is drawn to the beat. Shaped
    # like the piece itself rather than its bounding box, or a circle reveal would flash
    # a square.
    if new_idx >= 0 and new_tile_flash > 0.01:
        row, column = divmod(new_idx, columns)
        y0 = int(round(row * height / rows)) + seam
        y1 = int(round((row + 1) * height / rows)) - seam
        x0 = int(round(column * width / columns)) + seam
        x1 = int(round((column + 1) * width / columns)) - seam
        if y1 > y0 and x1 > x0:
            if shape == "circle":
                spot = np.zeros((height, width), dtype=np.uint8)
                cv2.ellipse(spot, ((x0 + x1) // 2, (y0 + y1) // 2),
                            (max(1, (x1 - x0) // 2), max(1, (y1 - y0) // 2)),
                            0.0, 0.0, 360.0, 1, thickness=-1)
                area = spot.astype(bool)
            else:
                area = np.zeros((height, width), dtype=bool)
                area[y0:y1, x0:x1] = True
            lifted = canvas[area].astype(np.float32) + (45.0 * new_tile_flash)
            canvas[area] = np.clip(lifted, 0, 255).astype(np.uint8)

    return canvas


def required_headroom(template: Template, margin: float = 0.04) -> float:
    """How large each picture must be loaded, as a multiple of the canvas.

    This is about resolution, not framing. Whatever the largest on-screen scale the
    template reaches, the picture is loaded at least that big so a zoom shows real
    detail rather than upscaled pixels. The renderer then divides by this figure, so a
    scale of 1.0 always means "exactly fills the frame" regardless of what was loaded.

    Under ``mirror`` the scale stays at 1.0 and the value comes out near 1.0, meaning no
    crop at all. Under ``zoom`` it rises to whatever the bounce demands.
    """
    worst = 1.0
    for clip in template.clips:
        animation = animations.get(clip.animation)
        duration = clip.animation_duration or clip.duration
        worst = max(worst, animations.peak_cover_scale(
            animation, duration, template.aspect, axis=clip.axis,
            intensity=clip.intensity, cover_mode=clip.cover_mode,
        ) if clip.cover_mode == "zoom" else 1.0)
        worst = max(worst, abs(clip.scale))
        # Static per-clip offsets still need covering whichever mode is in use.
        worst = max(worst, 1.0 + abs(clip.offset_y),
                    1.0 + abs(clip.offset_x) / max(template.aspect, 1e-6))

    # An arrival can zoom well past the animation's own range, and a picture loaded only
    # for the animation would show its pixels when the arrival pushes further.
    arrival_zoom = {"zoom_unveil": 2.4, "zoom_burst": 1.9, "scale_pop": 1.3,
                    "slide_in": 1.6}
    for clip in template.clips:
        worst = max(worst, arrival_zoom.get((clip.reveal_kind or "").lower(), 1.0))

    rotation = max((abs(clip.rotation) for clip in template.clips), default=0.0)
    for clip in template.clips:
        for action in animations.get(clip.animation).actions:
            if action.kind == "rotation":
                rotation = max(rotation, abs(float(action.start_value)),
                               abs(float(action.end_value)))
    rotation_allowance = math.sin(math.radians(min(45.0, rotation))) * 1.2

    return min(2.6, worst + rotation_allowance + margin)


def render_frame(template: Template, photos: PhotoSet, time_s: float,
                 canvas: np.ndarray | None = None) -> np.ndarray:
    """Compose one frame at ``time_s`` seconds.

    Three layers are combined, in the order the design lays them out: how the picture
    arrives, how it moves while it is up, and how it twitches on the beat. Only the middle
    one is a property of the picture alone -- an arrival needs to know what it is arriving
    over, and a beat reaction is applied to the finished frame -- so the layers are
    resolved here rather than inside the per-clip drawing.
    """
    width, height = template.width, template.height
    if canvas is None:
        canvas = np.zeros((height, width, 3), dtype=np.uint8)
    else:
        canvas[:] = 0

    clip = template.clip_at(time_s)
    if clip is None:
        return canvas

    arrival = _arrival(template, clip, time_s)
    canvas, state = _compose_clip(template, photos, clip, time_s, canvas,
                                  arrival=arrival)

    # What an arrival uncovers. A wipe over black looks like a fault; over the picture it
    # is replacing it looks deliberate, so the outgoing photo is held underneath.
    alpha = min(state.alpha, arrival.alpha)
    needs_under = alpha < 0.999 or arrival.mask is not None
    under: np.ndarray | None = None
    if needs_under:
        previous = template.clip_before(clip)
        if previous is not None:
            under, _ = _compose_clip(
                template, photos, previous,
                max(previous.start, previous.end - 1e-4), np.zeros_like(canvas))

    if arrival.mask is not None:
        base = under if under is not None else _dim(canvas, 0.14)
        canvas = _blend(canvas, base, arrival.mask)

    if alpha < 0.999:
        if under is not None:
            canvas = cv2.addWeighted(canvas, max(0.0, alpha), under,
                                     1.0 - max(0.0, alpha), 0.0)
        else:
            # Nothing precedes the first clip, so this one does fade up from black. That
            # reads as an opening rather than a fault.
            canvas = _dim(canvas, max(0.0, alpha))

    return _react(template, clip, time_s, canvas, arrival)


# ------------------------------------------------------------ layer 1: arrival

@dataclass
class Arrival:
    """How a picture is entering at this instant.

    Geometry is handed to the drawing step so the transform stays a single operation;
    masks and alpha are resolved by the caller, which knows what sits underneath.
    """

    #: Extra multiplier on scale, and extra offset in half-canvas-heights.
    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    #: 1.0 means fully opaque.
    alpha: float = 1.0
    #: Blur in pixels, for an arrival that sharpens into focus.
    blur: float = 0.0
    #: Per-pixel weight for this picture, 0 where it has not arrived yet.
    mask: np.ndarray | None = None
    #: A white flare across the cut, 0..1.
    flash: float = 0.0
    #: Directional smear, for a whip.
    smear: float = 0.0


def _ease_out(amount: float, power: float = 2.5) -> float:
    return 1.0 - (1.0 - max(0.0, min(1.0, amount))) ** power


def _arrival(template: Template, clip: Clip, time_s: float) -> Arrival:
    """Work out how far into its entrance this picture is, and what that looks like."""
    arrival = Arrival()
    kind = (clip.reveal_kind or "").lower()

    # A transition belongs to the boundary between two pictures. Rendering it on the
    # incoming side means no look-ahead is needed, and the result is the same thing seen
    # from the other direction.
    previous = template.clip_before(clip)
    handover = (previous.transition or "").lower() if previous is not None else ""
    handover_seconds = previous.transition_duration if previous is not None else 0.0
    if handover_seconds > 0:
        crossing = (time_s - clip.start) / handover_seconds
        if 0.0 <= crossing <= 1.0:
            if handover == "flash":
                arrival.flash = float((1.0 - crossing) ** 2)
            elif handover == "whip":
                arrival.smear = float((1.0 - crossing) ** 2)
                arrival.offset_x = float((1.0 - _ease_out(crossing)) * 0.25)
            elif handover == "dissolve" and not kind:
                arrival.alpha = min(arrival.alpha, float(crossing))

    span = clip.reveal_duration
    # A piece reveal is timed by the music rather than by a duration, and is drawn by the
    # piece masking, so it contributes no geometry of its own. Both it and a plain cut
    # still fall through to the covering step below, because a hand-over may already have
    # moved the picture and that gap has to be closed either way.
    geometric = bool(kind) and kind not in ("cut", "pieces") and span > 1e-6
    progress = (time_s - clip.start) / span if geometric else 1.0
    if geometric and 0.0 <= progress < 1.0:
        progress = max(0.0, progress)
        eased = _ease_out(progress)
        _apply_arrival_shape(arrival, kind, progress, eased, template)

    return _cover_travel(arrival, template)


def _apply_arrival_shape(arrival: "Arrival", kind: str, progress: float, eased: float,
                         template: Template) -> None:
    """Set the geometry, opacity or mask for one kind of arrival, mid-flight."""
    if kind == "scale_pop":
        # Overshoot: past the target and back, which is what gives it the snap.
        arrival.scale = 1.0 + 0.28 * (1.0 - eased) - 0.04 * math.sin(eased * math.pi)
    elif kind == "zoom_unveil":
        arrival.scale = 1.0 + 1.4 * (1.0 - _ease_out(progress, 1.8))
    elif kind == "zoom_burst":
        arrival.scale = 1.0 + 0.9 * (1.0 - _ease_out(progress, 4.0))
        shake = (1.0 - progress) ** 2 * 0.05
        arrival.offset_x = math.sin(progress * 42.0) * shake
        arrival.offset_y = math.cos(progress * 37.0) * shake
    elif kind == "slide_in":
        # Kept modest: covering the gap a slide opens costs zoom, and on a tall frame a
        # long slide would mean zooming so far in that the photo is mostly cropped away.
        arrival.offset_x = (1.0 - eased) * 0.28
    elif kind == "soft_focus":
        arrival.blur = (1.0 - _ease_out(progress, 1.6)) * 42.0
    elif kind == "fade":
        arrival.alpha = min(arrival.alpha, _ease_out(progress, 1.5))
    elif kind in ("shutter", "split", "circle_wipe", "diagonal_wipe"):
        arrival.mask = _wipe_mask(kind, template.width, template.height, eased)


def _cover_travel(arrival: "Arrival", template: Template) -> "Arrival":
    """Zoom in by as much as the picture has been moved, so no gap is left behind.

    Anything that shifts the picture off centre -- an arrival that slides, a hand-over that
    whips, a shake -- would otherwise expose the background at the trailing edge. Scaling
    up to compensate is the same bargain the cover modes already make for the animation
    layer.

    Both offsets are in half-canvas-heights, so a horizontal one has to be converted into a
    fraction of the width first. On a tall frame that is a much larger number, and treating
    the two axes alike undercompensates badly enough to leave a visible black strip.

    Every arrival passes through here, including the ones with no geometry of their own,
    because a hand-over can have moved the picture regardless of how it arrives.
    """
    aspect = template.width / max(1, template.height)
    travel = abs(arrival.offset_x) / max(aspect, 1e-6) + abs(arrival.offset_y)
    if travel > 0:
        arrival.scale = max(arrival.scale, 1.0 + travel)
    return arrival


#: Coordinate grids depend only on the frame size, so they are built once and reused. At
#: 1080x1920 rebuilding them per frame is a measurable share of the render.
_GRID_CACHE: dict[tuple[int, int, str], tuple[np.ndarray, np.ndarray]] = {}


def _grids(width: int, height: int, span: str) -> tuple[np.ndarray, np.ndarray]:
    key = (width, height, span)
    cached = _GRID_CACHE.get(key)
    if cached is None:
        low, high = (0.0, 1.0) if span == "unit" else (-1.0, 1.0)
        ys = np.linspace(low, high, height, dtype=np.float32)[:, None]
        xs = np.linspace(low, high, width, dtype=np.float32)[None, :]
        cached = (ys, xs)
        _GRID_CACHE[key] = cached
    return cached


def _wipe_mask(kind: str, width: int, height: int, amount: float) -> np.ndarray:
    """A 0..1 weight per pixel describing how much of the picture has arrived."""
    amount = max(0.0, min(1.0, amount))
    ys, xs = _grids(width, height, "unit")
    # A soft edge stops the boundary looking like a cut-out.
    soft = 0.06

    if kind == "shutter":
        strips = 10
        # Each blind opens from its own centre, staggered so they cascade.
        local = (ys * strips) % 1.0
        stagger = np.floor(ys * strips) / strips
        opened = np.clip((amount - stagger * 0.35) / 0.65, 0.0, 1.0)
        return np.clip((opened - np.abs(local - 0.5) * 2.0) / soft + 1.0,
                       0.0, 1.0).astype(np.float32) * np.ones_like(xs)

    if kind == "split":
        # The frame parts from the middle outward.
        return np.clip((amount - np.abs(xs - 0.5) * 2.0) / soft + 1.0,
                       0.0, 1.0).astype(np.float32) * np.ones_like(ys)

    if kind == "circle_wipe":
        aspect = width / max(1, height)
        radius = np.sqrt(((xs - 0.5) * aspect) ** 2 + (ys - 0.5) ** 2)
        limit = amount * (0.5 * math.sqrt(1.0 + aspect ** 2) + soft)
        return np.clip((limit - radius) / soft + 0.5, 0.0, 1.0).astype(np.float32)

    # diagonal_wipe
    diagonal = (xs + ys) / 2.0
    return np.clip((amount * (1.0 + soft) - diagonal) / soft + 0.5,
                   0.0, 1.0).astype(np.float32)


def _blend(top: np.ndarray, bottom: np.ndarray, mask: np.ndarray) -> np.ndarray:
    weight = mask[:, :, None]
    return (top.astype(np.float32) * weight +
            bottom.astype(np.float32) * (1.0 - weight)).astype(np.uint8)


def _dim(frame: np.ndarray, amount: float) -> np.ndarray:
    return (frame.astype(np.float32) * max(0.0, amount)).astype(np.uint8)


# ------------------------------------------------------------ layer 3: reactions

def _beat_phase(clip: Clip, time_s: float, window: float) -> float:
    """How fresh the most recent beat is, 1.0 on the beat and 0.0 once spent.

    Reactions are triggered by beats baked into the clip rather than by sampling a curve,
    so a render needs no access to the music analysis and is reproducible from the plan
    alone.
    """
    if not clip.micro_beats or window <= 1e-6:
        return 0.0
    best = 0.0
    for beat in clip.micro_beats:
        since = time_s - beat
        if 0.0 <= since <= window:
            best = max(best, (1.0 - since / window) ** 2)
    return best


def _react(template: Template, clip: Clip, time_s: float, canvas: np.ndarray,
           arrival: Arrival) -> np.ndarray:
    """Apply the beat reactions and the flare across a cut.

    Everything here is deliberately small. The design is firm that this layer should be
    felt rather than seen: a few per cent, not a fifth. The reactions that change geometry
    are handled during drawing; these are the ones that change the finished pixels.
    """
    strength = max(0.0, min(1.0, clip.micro_intensity))
    active = set(clip.micro or ())

    if strength > 0.01 and active:
        if "brightness_flash" in active:
            hit = _beat_phase(clip, time_s, 0.22)
            if hit > 0.01:
                # Saturating integer add, which is both correct at the top end and much
                # cheaper than a round trip through float.
                canvas = cv2.add(canvas, float(26.0 * hit * strength))
        if "vignette_pulse" in active:
            hit = _beat_phase(clip, time_s, 0.40)
            if hit > 0.01:
                canvas = _vignette(canvas, 0.22 * hit * strength)
        if "chromatic" in active:
            hit = _beat_phase(clip, time_s, 0.25)
            shift = int(round(5.0 * hit * strength))
            if shift >= 1:
                canvas = _split_channels(canvas, shift)

    if arrival.flash > 0.01:
        canvas = cv2.add(canvas, float(210.0 * arrival.flash))
    return canvas


#: The falloff shape is the same every frame; only how far it is pushed changes.
_FALLOFF_CACHE: dict[tuple[int, int], np.ndarray] = {}


def _falloff(width: int, height: int) -> np.ndarray:
    cached = _FALLOFF_CACHE.get((width, height))
    if cached is None:
        ys, xs = _grids(width, height, "signed")
        radius = np.sqrt(xs ** 2 + ys ** 2) / math.sqrt(2.0)
        cached = np.clip(radius, 0.0, 1.0) ** 2
        _FALLOFF_CACHE[(width, height)] = cached
    return cached


def _vignette(frame: np.ndarray, amount: float) -> np.ndarray:
    height, width = frame.shape[:2]
    shade = 1.0 - amount * _falloff(width, height)
    return (frame.astype(np.float32) * shade[:, :, None]).astype(np.uint8)


def _split_channels(frame: np.ndarray, shift: int) -> np.ndarray:
    """Part the red and blue channels, the look of a lens under stress."""
    out = frame.copy()
    out[:, shift:, 2] = frame[:, :-shift, 2]
    out[:, :-shift, 0] = frame[:, shift:, 0]
    return out


def _compose_clip(template: Template, photos: PhotoSet, clip: Clip, time_s: float,
                  canvas: np.ndarray,
                  arrival: "Arrival | None" = None
                  ) -> tuple[np.ndarray, animations.AnimState]:
    """Draw one clip into ``canvas``, returning it with the animation state used.

    Alpha is deliberately left to the caller: blending needs to know what sits beneath,
    which is a decision about the whole frame rather than this one layer.
    """
    width, height = template.width, template.height
    photo = photos.for_slot(clip.slot)
    image = photo.image
    arrival = arrival or Arrival()

    animation = animations.get(clip.animation)
    duration = clip.animation_duration or clip.duration
    progress = 0.0 if duration <= 0 else (time_s - clip.start) / duration
    state = animations.evaluate(
        animation, progress, duration, template.aspect,
        axis=clip.axis, intensity=clip.intensity, cover_mode=clip.cover_mode,
    )

    # Layer 3, the parts of a beat reaction that move the picture rather than recolour it.
    # Kept here so the whole thing stays one affine transform instead of a resample.
    pulse, shake_x, shake_y = 1.0, 0.0, 0.0
    strength = max(0.0, min(1.0, clip.micro_intensity))
    if strength > 0.01 and clip.micro:
        if "zoom_pulse" in clip.micro:
            pulse += 0.035 * strength * _beat_phase(clip, time_s, 0.20)
        if "bass_throb" in clip.micro:
            # A swell rather than a hit: slower, and it never fully releases.
            pulse += 0.025 * strength * _beat_phase(clip, time_s, 0.55)
        if "shake" in clip.micro:
            hit = _beat_phase(clip, time_s, 0.20)
            shake_x = math.sin(time_s * 78.0) * 0.012 * strength * hit
            shake_y = math.cos(time_s * 91.0) * 0.012 * strength * hit
            # A shake moves the picture, so like any other movement it has to be paid for
            # with a little zoom or it would show the background at the frame edge.
            aspect = template.width / max(1, template.height)
            pulse += abs(shake_x) / max(aspect, 1e-6) + abs(shake_y)

    # Position offsets are in half-canvas-heights, matching CapCut's normalised space.
    unit = height / 2.0
    offset_x = (state.offset_x + clip.offset_x + arrival.offset_x + shake_x) * unit
    offset_y = (state.offset_y + clip.offset_y + arrival.offset_y + shake_y) * unit

    # Pictures are loaded oversized for resolution, so divide that out: a scale of 1.0
    # then means "exactly fills the frame", whatever was loaded.
    fit = 1.0 / max(photos.headroom, 1e-6)
    scale = fit * clip.scale * state.scale_x * arrival.scale * pulse
    rotation = clip.rotation + state.rotation_deg

    # A zoom aims at the subject rather than the centre of the frame. Without a subject
    # detector the choreographer supplies a rule-of-thirds point, and the picture is
    # nudged so that point sits mid-frame in proportion to how far it is zoomed in.
    if scale * photos.headroom > 1.02:
        pull = min(1.0, (scale * photos.headroom - 1.0))
        offset_x -= (clip.focal_x - 0.5) * width * pull
        offset_y -= (clip.focal_y - 0.5) * height * pull

    source_w, source_h = photo.size
    matrix = cv2.getRotationMatrix2D((source_w / 2.0, source_h / 2.0), rotation, scale)
    # Move the picture's centre to the canvas centre, then apply the animation offset.
    matrix[0, 2] += (width / 2.0) - (source_w / 2.0) + offset_x
    matrix[1, 2] += (height / 2.0) - (source_h / 2.0) + offset_y

    if clip.flip_horizontal:
        matrix[0, 0] *= -1
        matrix[0, 1] *= -1
        matrix[0, 2] = width - matrix[0, 2]
    if clip.flip_vertical:
        matrix[1, 0] *= -1
        matrix[1, 1] *= -1
        matrix[1, 2] = height - matrix[1, 2]

    # Mirror mode reflects the picture across its own edge to fill whatever gap the
    # movement opens. Zoom mode should never leave a gap at all, so it gets constant
    # black: if a gap ever does appear it shows up as an honest fault rather than being
    # papered over.
    border = (cv2.BORDER_REFLECT_101 if clip.cover_mode == "mirror"
              else cv2.BORDER_CONSTANT)
    cv2.warpAffine(
        image, matrix, (width, height), dst=canvas,
        flags=cv2.INTER_LINEAR, borderMode=border, borderValue=(0, 0, 0),
    )

    shown = pieces_shown(clip, time_s)
    if clip.reveal_total > 0 and shown < clip.reveal_total:
        canvas = _apply_reveal(canvas, clip, progress, shown_override=shown)

    if state.blur_pixels > 1.0:
        canvas = _directional_blur(canvas, state.blur_pixels, state.blur_direction)

    # A whip is a smear along the direction of travel; an arrival that sharpens into
    # focus is an even blur. Both are applied after the transform so they act on what is
    # actually on screen.
    if arrival.smear > 0.01:
        canvas = _directional_blur(canvas, 4.0 + 26.0 * arrival.smear, (1.0, 0.0))
    if arrival.blur > 1.0:
        radius = int(arrival.blur) | 1
        canvas = cv2.GaussianBlur(canvas, (radius, radius), 0)

    return canvas, state


def _open_encoder(output: Path, width: int, height: int, fps: float,
                  audio: Path | None, audio_start: float,
                  duration: float, crf: int) -> subprocess.Popen:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        config.FFMPEG, "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}", "-r", f"{fps}",
        "-i", "-",
    ]
    if audio is not None:
        # Trim the music to the template's own span, from wherever the edit started.
        command += ["-ss", f"{max(0.0, audio_start)}", "-i", str(audio)]
        command += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k",
                    "-shortest"]
    else:
        command += ["-an"]
    command += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-t", f"{duration:.3f}",
        str(output),
    ]
    kwargs: dict = dict(stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(command, **kwargs)


def render(template: Template, photos: PhotoSet, output: str | Path,
           progress: ProgressFn | None = None,
           scale: float = 1.0, crf: int = 18,
           with_audio: bool = True,
           frame_dir: str | Path | None = None,
           should_cancel: Callable[[], bool] | None = None) -> RenderResult:
    """Render the whole template.

    ``scale`` renders at a fraction of the template's canvas, which is how previews are
    produced. Using the same code path for preview and final output means what is
    previewed is what gets rendered, only smaller.
    """
    warnings: list[str] = []
    output = Path(output)

    width = max(2, int(round(template.width * scale)) // 2 * 2)
    height = max(2, int(round(template.height * scale)) // 2 * 2)

    working = template
    if scale != 1.0:
        working = Template.from_dict(template.to_dict())
        working.width, working.height = width, height

    headroom = required_headroom(template)
    photo_set = photos
    if (width, height) != photos.canvas or abs(photos.headroom - headroom) > 0.01:
        photo_set = PhotoSet([p for p in photos.paths], (width, height), headroom)

    audio: Path | None = None
    if with_audio and template.audio_path:
        candidate = Path(template.audio_path)
        if candidate.exists():
            audio = candidate
        else:
            warnings.append(f"audio not found, rendering silent: {candidate}")

    frames = max(1, int(round(template.duration * template.fps)))
    frame_folder = Path(frame_dir) if frame_dir else None
    if frame_folder:
        frame_folder.mkdir(parents=True, exist_ok=True)

    encoder = _open_encoder(output, width, height, template.fps, audio,
                            template.audio_start, template.duration, crf)

    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    started = time.perf_counter()
    written = 0

    try:
        assert encoder.stdin is not None
        for index in range(frames):
            if should_cancel and should_cancel():
                warnings.append(f"cancelled after {written} frames")
                break
            time_s = index / template.fps
            frame = render_frame(working, photo_set, time_s, canvas)
            encoder.stdin.write(frame.tobytes())
            if frame_folder:
                cv2.imwrite(str(frame_folder / f"frame_{index:05d}.png"), frame)
            written += 1
            if progress and (index % 5 == 0 or index == frames - 1):
                progress(index + 1, frames, f"frame {index}")
    finally:
        if encoder.stdin:
            try:
                encoder.stdin.close()
            except OSError:
                pass
        try:
            encoder.wait(timeout=180)
        except subprocess.TimeoutExpired:
            encoder.kill()
            encoder.wait()

    if encoder.returncode not in (0, None):
        detail = ""
        if encoder.stderr:
            detail = encoder.stderr.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"ffmpeg failed ({encoder.returncode}): {detail}")

    elapsed = time.perf_counter() - started
    return RenderResult(
        output=output,
        frames=written,
        elapsed_seconds=elapsed,
        seconds_per_frame=elapsed / max(1, written),
        audio_muxed=audio is not None,
        warnings=warnings,
    )


def contact_sheet(template: Template, photos: PhotoSet, count: int = 8,
                  width: int = 1600) -> np.ndarray:
    """A grid of frames spread across the timeline, for a quick look."""
    times = [template.duration * i / max(1, count - 1) for i in range(count)]
    columns = 4
    rows = (count + columns - 1) // columns
    cell_w = width // columns
    cell_h = max(1, int(round(cell_w * template.height / template.width)))
    sheet = np.zeros((rows * cell_h, columns * cell_w, 3), dtype=np.uint8)

    for position, time_s in enumerate(times):
        frame = render_frame(template, photos, min(time_s, template.duration - 1e-3))
        thumb = cv2.resize(frame, (cell_w, cell_h), interpolation=cv2.INTER_AREA)
        row, column = divmod(position, columns)
        sheet[row * cell_h:(row + 1) * cell_h,
              column * cell_w:(column + 1) * cell_w] = thumb
        label = f"{time_s:.2f}s"
        origin = (column * cell_w + 8, row * cell_h + 22)
        cv2.putText(sheet, label, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(sheet, label, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (255, 255, 255), 1, cv2.LINE_AA)
    return sheet
