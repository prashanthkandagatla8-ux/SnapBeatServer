"""The animation library.

Each animation is data, not code: a list of actions with a window, an easing curve and
start/end values. The definitions are transcribed from the effect bundles CapCut caches
on disk, so the motion matches rather than approximates.

Adding an animation means adding one entry here. ``tools/extract_animation.py`` reads a
cached bundle and prints a ready-made entry.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Literal

from .easing import LINEAR, ease, ease_velocity

#: Direction the motion travels. A leading minus reverses it, so "-y" enters from the
#: bottom and rises while "y" enters from the top and falls.
Axis = Literal["x", "-x", "y", "-y"]


@dataclass(frozen=True)
class Action:
    """One animated property over one window of normalised animation time."""

    kind: Literal["position", "rotation", "scale", "alpha", "blur"]
    start_time: float                       # 0..1 of the animation's duration
    end_time: float
    easing: tuple[float, float, float, float] = LINEAR
    #: For position/scale: (x, y). For rotation: degrees about z. For alpha: 0..1.
    start_value: tuple[float, float] | float = 0.0
    end_value: tuple[float, float] | float = 0.0
    #: Blur only.
    blur_intensity: float = 0.0
    blur_type: int = 1                      # 1 directional, 2 radial/scale
    blur_direction: tuple[float, float] = (1.0, 0.0)

    def window(self) -> float:
        return max(1e-6, self.end_time - self.start_time)

    def local_progress(self, t: float) -> float:
        """Linear progress within this action's own window, clamped outside it."""
        if t <= self.start_time:
            return 0.0
        if t >= self.end_time:
            return 1.0
        return (t - self.start_time) / self.window()


#: How the frame is kept filled while a picture is displaced off centre.
#:
#: ``mirror``  the picture sits at exactly frame size, and whatever gap the movement
#:             opens up is filled by reflecting the picture across its own edge. No
#:             cropping at all, and the fill reads as a soft continuation.
#: ``zoom``    the picture scales up by exactly as much as the displacement requires,
#:             so it always covers. Nothing is mirrored and nothing is cropped while it
#:             is centred; the zoom breathes with the bounce.
CoverMode = Literal["mirror", "zoom"]

COVER_MODES: tuple[str, ...] = ("mirror", "zoom")


@dataclass
class Animation:
    name: str
    actions: list[Action] = field(default_factory=list)
    #: Which way the motion travels by default.
    axis: Axis = "x"
    notes: str = ""
    source: str = ""


@dataclass
class AnimState:
    """Evaluated animation at one instant, in normalised canvas units."""

    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation_deg: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    alpha: float = 1.0
    blur_pixels: float = 0.0
    blur_direction: tuple[float, float] = (1.0, 0.0)
    blur_type: int = 1


# --------------------------------------------------------------------- library

#: Transcribed from CapCut effect resource 6811007755785081357 (category "Trending-1").
#: Four concurrent actions: an ease-out slide in, an ease-in slide back out, a linear
#: counter-rotation across the whole clip, and directional blur over the first half.
PENDULUM_1 = Animation(
    name="Pendulum 1",
    axis="x",
    notes=(
        "Swings in from off-centre, holds briefly, swings back out, counter-rotating "
        "3 degrees each way. Blur runs only during the incoming half."
    ),
    source="CapCut effect 6811007755785081357 / Transform.lua",
    actions=[
        Action(
            kind="position",
            start_time=0.0, end_time=0.5,
            easing=(0.21, 0.93, 0.48, 0.97),
            start_value=(-0.6, 0.0),
            end_value=(-0.1, 0.0),
        ),
        Action(
            kind="position",
            start_time=0.5, end_time=1.0,
            easing=(0.73, 0.02, 0.86, 0.1),
            start_value=(-0.1, 0.0),
            end_value=(-0.6, 0.0),
        ),
        Action(
            kind="rotation",
            start_time=0.0, end_time=1.0,
            easing=LINEAR,
            start_value=3.0,
            end_value=-3.0,
        ),
        Action(
            kind="blur",
            start_time=0.0, end_time=0.5,
            easing=(0.21, 0.93, 0.48, 0.97),
            blur_intensity=2.0,
            blur_type=1,
            blur_direction=(1.0, 0.0),
        ),
    ],
)

#: No animation at all, for clips that should sit still.
STATIC = Animation(name="Static", actions=[], notes="No movement.")

#: Straight cuts on the beat and nothing else. Sounds plain, but on a strong track the
#: cut itself carries the rhythm, and it suits photos where movement would distract.
CUT = Animation(
    name="Cut",
    actions=[],
    notes="Hard cut on every beat. No movement at all.",
)

#: A scale punch. The most transferable beat effect there is: it reads on any music
#: because it lands on the transient rather than travelling across the frame.
PULSE = Animation(
    name="Pulse",
    axis="x",
    notes="Snaps in slightly oversized and settles. Lands on the beat, no drift.",
    actions=[
        Action(
            kind="scale",
            start_time=0.0, end_time=0.55,
            easing=(0.16, 0.9, 0.36, 1.0),
            start_value=(1.14, 1.14),
            end_value=(1.0, 1.0),
        ),
    ],
)

#: Enters from one side and stays. Unlike the pendulum it does not swing back out, so
#: consecutive clips feel like a deck of cards rather than a bounce.
SLIDE = Animation(
    name="Slide",
    axis="x",
    notes="Slides in from one side and settles. Direction sets which side.",
    actions=[
        Action(
            kind="position",
            start_time=0.0, end_time=0.45,
            easing=(0.17, 0.89, 0.32, 1.0),
            start_value=(-0.55, 0.0),
            end_value=(0.0, 0.0),
        ),
        Action(
            kind="blur",
            start_time=0.0, end_time=0.45,
            easing=(0.17, 0.89, 0.32, 1.0),
            blur_intensity=1.4,
            blur_type=1,
            blur_direction=(1.0, 0.0),
        ),
    ],
)

#: A hard, fast throw with heavy smear. Best on sharp percussive tracks; on gentle music
#: it is too aggressive, which is why it is a separate choice rather than a default.
WHIP = Animation(
    name="Whip",
    axis="x",
    notes="Fast throw in with a strong motion smear. Suits percussive tracks.",
    actions=[
        Action(
            kind="position",
            start_time=0.0, end_time=0.28,
            easing=(0.1, 0.95, 0.25, 1.0),
            start_value=(-1.05, 0.0),
            end_value=(0.0, 0.0),
        ),
        Action(
            kind="rotation",
            start_time=0.0, end_time=0.35,
            easing=(0.1, 0.95, 0.25, 1.0),
            start_value=-5.0,
            end_value=0.0,
        ),
        Action(
            kind="blur",
            start_time=0.0, end_time=0.30,
            easing=(0.1, 0.95, 0.25, 1.0),
            blur_intensity=4.5,
            blur_type=1,
            blur_direction=(1.0, 0.0),
        ),
    ],
)

#: Slow drift across the whole clip. Deliberately gentle: it gives still photos some life
#: on slow music without competing with the picture.
DRIFT = Animation(
    name="Drift",
    axis="x",
    notes="Slow continuous glide with a gentle zoom. Good for slow or sparse music.",
    actions=[
        Action(
            kind="position",
            start_time=0.0, end_time=1.0,
            easing=LINEAR,
            start_value=(-0.12, 0.0),
            end_value=(0.12, 0.0),
        ),
        Action(
            kind="scale",
            start_time=0.0, end_time=1.0,
            easing=LINEAR,
            start_value=(1.06, 1.06),
            end_value=(1.14, 1.14),
        ),
    ],
)

#: A playful scale rebound: snaps in oversized, settles, springs up once, settles again.
#: More energetic than Pulse because the rebound gives it a bounce feel.
#:
#: Every value stays at or above 1.0. A scale below 1.0 means the picture is smaller than
#: the frame, which shows as black edges under the zoom cover mode, so the spring is taken
#: upwards rather than by dipping under the target.
BOUNCE = Animation(
    name="Bounce",
    axis="x",
    notes="Scale rebound: lands big, settles, springs up once more, then rests. Playful.",
    actions=[
        Action(
            kind="scale",
            start_time=0.0, end_time=0.32,
            easing=(0.16, 0.84, 0.44, 1.0),   # no control point past 1.0, so no undershoot
            start_value=(1.24, 1.24),
            end_value=(1.0, 1.0),
        ),
        Action(
            kind="scale",
            start_time=0.32, end_time=0.48,
            easing=(0.33, 0.0, 0.67, 1.0),
            start_value=(1.0, 1.0),
            end_value=(1.06, 1.06),
        ),
        Action(
            kind="scale",
            start_time=0.48, end_time=0.75,
            easing=(0.33, 0.0, 0.67, 1.0),
            start_value=(1.06, 1.06),
            end_value=(1.0, 1.0),
        ),
    ],
)

#: A smooth cross-fade. The renderer holds the outgoing picture underneath and fades this
#: one up over it, so the two cross over on the beat without the frame ever going dark.
#: Only the very first clip has nothing beneath it, which reads as an opening fade.
FADE = Animation(
    name="Fade",
    axis="x",
    notes="Cross-dissolve from the previous photo over the first third of the clip.",
    actions=[
        Action(
            kind="alpha",
            start_time=0.0, end_time=0.35,
            easing=(0.42, 0.0, 0.58, 1.0),     # ease-in-out
            start_value=0.0,
            end_value=1.0,
        ),
    ],
)

#: A slow zoom into the centre across the whole clip. Ken Burns without the pan: it gives
#: still photos some cinematic life on slower or ambient music.
ZOOM_IN = Animation(
    name="Zoom In",
    axis="x",
    notes="Slow cinematic zoom into the centre. Good for emotional or ambient tracks.",
    actions=[
        Action(
            kind="scale",
            start_time=0.0, end_time=1.0,
            easing=(0.25, 0.1, 0.25, 1.0),     # ease-in-out
            start_value=(1.0, 1.0),
            end_value=(1.18, 1.18),
        ),
    ],
)

#: A gentle rotation with a zoom, for a more dynamic feel. The rotation is slight enough
#: not to distract but enough to add energy.
SPIN = Animation(
    name="Spin",
    axis="x",
    notes="Gentle rotation with scale, adding rotational energy. Best on energetic tracks.",
    actions=[
        Action(
            kind="rotation",
            start_time=0.0, end_time=1.0,
            easing=(0.25, 0.1, 0.25, 1.0),
            start_value=-4.0,
            end_value=4.0,
        ),
        Action(
            kind="scale",
            start_time=0.0, end_time=1.0,
            easing=LINEAR,
            start_value=(1.12, 1.12),
            end_value=(1.06, 1.06),
        ),
    ],
)

#: The reverse of Zoom In: starts wide and eases back to the frame. Pulls the eye into the
#: picture as it settles, which suits a photo with a clear subject.
ZOOM_OUT = Animation(
    name="Zoom Out",
    axis="x",
    notes="Starts wide and eases back to the frame. Calmer than Punch In, still directed.",
    actions=[
        Action(
            kind="scale",
            start_time=0.0, end_time=1.0,
            easing=(0.25, 0.1, 0.25, 1.0),     # ease-in-out
            start_value=(1.20, 1.20),
            end_value=(1.0, 1.0),
        ),
    ],
)

#: A hard, fast scale snap. Where Pulse breathes, this hits: most of the movement is over
#: in the first fifth of the clip, so it lands hard on a strong beat.
PUNCH_IN = Animation(
    name="Punch In",
    axis="x",
    notes="Hard fast scale snap that lands on the beat, then holds. Best on sharp drums.",
    actions=[
        Action(
            kind="scale",
            start_time=0.0, end_time=0.18,
            easing=(0.1, 0.9, 0.2, 1.0),
            start_value=(1.32, 1.32),
            end_value=(1.0, 1.0),
        ),
    ],
)

#: A slow lean one way and back, like a held breath. Rotation only, so it reads as a drift
#: of the horizon rather than movement of the subject.
SWAY = Animation(
    name="Sway",
    axis="x",
    notes="Slow tilt one way then back. Unhurried; suits ballads and long clips.",
    actions=[
        Action(
            kind="rotation",
            start_time=0.0, end_time=0.5,
            easing=(0.37, 0.0, 0.63, 1.0),
            start_value=-2.5,
            end_value=2.5,
        ),
        Action(
            kind="rotation",
            start_time=0.5, end_time=1.0,
            easing=(0.37, 0.0, 0.63, 1.0),
            start_value=2.5,
            end_value=-2.5,
        ),
    ],
)

#: Enters travelling and keeps going, passing through rather than stopping. Reads as a
#: camera whip between two photos when clips are short.
GLIDE = Animation(
    name="Glide",
    axis="x",
    notes="Travels steadily across the whole clip without settling. Keeps motion constant.",
    actions=[
        Action(
            kind="position",
            start_time=0.0, end_time=1.0,
            easing=LINEAR,
            start_value=(-0.16, 0.0),
            end_value=(0.16, 0.0),
        ),
    ],
)

LIBRARY: dict[str, Animation] = {
    PENDULUM_1.name: PENDULUM_1,
    CUT.name: CUT,
    PULSE.name: PULSE,
    SLIDE.name: SLIDE,
    WHIP.name: WHIP,
    DRIFT.name: DRIFT,
    BOUNCE.name: BOUNCE,
    FADE.name: FADE,
    ZOOM_IN.name: ZOOM_IN,
    ZOOM_OUT.name: ZOOM_OUT,
    PUNCH_IN.name: PUNCH_IN,
    SWAY.name: SWAY,
    GLIDE.name: GLIDE,
    SPIN.name: SPIN,
    STATIC.name: STATIC,
}

#: Offered in the interface, in the order they are worth trying. "Static" is an internal
#: fallback for unrecognised names and is not a choice.
SELECTABLE = (CUT.name, PULSE.name, PENDULUM_1.name, SLIDE.name, WHIP.name,
              DRIFT.name, BOUNCE.name, FADE.name, ZOOM_IN.name, ZOOM_OUT.name,
              PUNCH_IN.name, SWAY.name, GLIDE.name, SPIN.name)


def _squash(text: str) -> str:
    """Reduce a name to comparable form: lowercase, alphanumerics only.

    Animation names travel through CapCut JSON, command lines and shells, any of which
    may alter spacing or case. Matching on the alphanumeric core means "Pendulum 1",
    "pendulum1" and " Pendulum 1" all resolve to the same animation instead of silently
    falling back to no movement.
    """
    return "".join(ch for ch in text.lower() if ch.isalnum())


def get(name: str | None) -> Animation:
    """Look up an animation, falling back to static rather than failing a render."""
    if not name:
        return STATIC
    if name in LIBRARY:
        return LIBRARY[name]
    wanted = _squash(name)
    for key, animation in LIBRARY.items():
        if _squash(key) == wanted:
            return animation
    return STATIC


def resolve_name(name: str | None) -> str | None:
    """The library's canonical name for a possibly-mangled input."""
    if not name:
        return None
    animation = get(name)
    return animation.name if animation is not STATIC else None


def known_names() -> list[str]:
    return sorted(LIBRARY)


# -------------------------------------------------------------------- evaluate


def _rotate_axis(x: float, y: float, axis: Axis) -> tuple[float, float]:
    """Remap a horizontal motion onto the requested axis and direction.

    Pendulum-style animations are authored to travel along X. The CapCut tutorial gets a
    vertical bounce by rotating every photo 90 degrees, exporting, then rotating the
    finished video back, which costs an extra export and crops the frame. Remapping the
    axis here achieves the same motion directly.

    Screen Y grows downward, so plain "y" makes a picture enter from the top and fall.
    "-y" reverses it to enter from the bottom and rise.
    """
    if axis.lstrip("-") == "y":
        x, y = y, x
    if axis.startswith("-"):
        return -x, -y
    return x, y


def cover_scale_for(offset_x: float, offset_y: float, aspect: float,
                    rotation_deg: float = 0.0) -> float:
    """The smallest scale that still covers the frame at this displacement and rotation.

    Two things can open a gap. Translation: offsets are in half-canvas-heights, so an
    offset of 0.6 shifts the picture 30% of the frame, and only a picture ``1 + 0.6``
    times frame size still reaches the far edge. Rotation: a tilted rectangle no longer
    covers the corners, and the shortfall depends on the frame's shape, which is why
    both orientations are considered and the larger taken.

    The two factors are multiplied. That is marginally more than strictly necessary, but
    erring the other way shows as a black wedge at the frame edge.
    """
    needed = max(1.0,
                 1.0 + abs(offset_y),
                 1.0 + abs(offset_x) / max(aspect, 1e-6))

    if rotation_deg:
        radians = math.radians(min(45.0, abs(rotation_deg)))
        cos_t, sin_t = abs(math.cos(radians)), abs(math.sin(radians))
        safe_aspect = max(aspect, 1e-6)
        needed *= max(1.0,
                      cos_t + sin_t / safe_aspect,
                      cos_t + sin_t * safe_aspect)
    return needed


def evaluate(animation: Animation, progress: float, duration: float,
             aspect: float, axis: Axis | None = None,
             intensity: float = 1.0,
             cover_mode: CoverMode = "mirror") -> AnimState:
    """Evaluate ``animation`` at normalised ``progress`` (0..1) of ``duration`` seconds.

    ``aspect`` is canvas width divided by height; CapCut scales horizontal offsets by it
    so a move reads the same distance on screen regardless of frame shape.

    ``cover_mode`` decides how the frame stays filled. Under ``zoom`` the scale is
    derived from the displacement rather than authored, so the picture is never cropped
    while centred and never leaves a gap while moving.
    """
    axis = axis or animation.axis
    state = AnimState()
    progress = min(1.0, max(0.0, progress))

    for action in animation.actions:
        # Actions outside their window still pin their end value, matching CapCut's
        # behaviour of leaving a completed tween at its final position.
        amount = ease(action.easing, action.local_progress(progress))

        if action.kind == "position":
            sx, sy = action.start_value                      # type: ignore[misc]
            ex, ey = action.end_value                        # type: ignore[misc]
            if progress < action.start_time:
                continue
            x = (sx + (ex - sx) * amount) * intensity
            y = (sy + (ey - sy) * amount) * intensity
            x, y = _rotate_axis(x, y, axis)
            # Horizontal travel is aspect-corrected, exactly as the bundle does.
            state.offset_x = x * aspect
            state.offset_y = y

        elif action.kind == "rotation":
            start = float(action.start_value)                # type: ignore[arg-type]
            end = float(action.end_value)                    # type: ignore[arg-type]
            state.rotation_deg = (start + (end - start) * amount) * intensity

        elif action.kind == "scale":
            sx, sy = action.start_value                      # type: ignore[misc]
            ex, ey = action.end_value                        # type: ignore[misc]
            if progress < action.start_time:
                continue
            state.scale_x = sx + (ex - sx) * amount
            state.scale_y = sy + (ey - sy) * amount

        elif action.kind == "alpha":
            start = float(action.start_value)                # type: ignore[arg-type]
            end = float(action.end_value)                    # type: ignore[arg-type]
            if progress < action.start_time:
                continue
            state.alpha = start + (end - start) * amount

        elif action.kind == "blur":
            if progress > action.end_time:
                state.blur_pixels = 0.0
                continue
            # CapCut sets blurStep = intensity / (duration * window) and tweens it to
            # zero along the same curve as the move, so the smear fades as it settles.
            window_seconds = max(1e-6, duration * action.window())
            peak = action.blur_intensity / window_seconds
            state.blur_pixels = peak * (1.0 - amount) * intensity
            direction = _rotate_axis(*action.blur_direction, axis)
            state.blur_direction = direction
            state.blur_type = action.blur_type

    if cover_mode == "zoom":
        # Applied after the actions so it sees the final offset and rotation, and
        # multiplied in rather than replacing any authored scale.
        needed = cover_scale_for(state.offset_x, state.offset_y, aspect,
                                 state.rotation_deg)
        state.scale_x *= needed
        state.scale_y *= needed

    return state


def peak_cover_scale(animation: Animation, duration: float, aspect: float,
                     axis: Axis | None = None, intensity: float = 1.0,
                     cover_mode: CoverMode = "mirror", steps: int = 32) -> float:
    """Largest scale the animation reaches, used to decide loading resolution.

    Pictures are loaded at this size so that a zoom shows real detail instead of
    interpolating pixels that were thrown away at load time.
    """
    worst = 1.0
    for step in range(steps):
        state = evaluate(animation, step / max(1, steps - 1), duration, aspect,
                         axis=axis, intensity=intensity, cover_mode=cover_mode)
        worst = max(worst, state.scale_x, state.scale_y,
                    cover_scale_for(state.offset_x, state.offset_y, aspect,
                                    state.rotation_deg))
    return worst


def sample(animation: Animation, steps: int, duration: float, aspect: float,
           axis: Axis | None = None) -> list[AnimState]:
    """Evaluate across the whole animation, for plotting or verification."""
    return [
        evaluate(animation, step / max(1, steps - 1), duration, aspect, axis)
        for step in range(steps)
    ]


def describe(animation: Animation) -> Iterable[str]:
    yield f"{animation.name}  (default axis {animation.axis})"
    if animation.notes:
        yield f"  {animation.notes}"
    if animation.source:
        yield f"  source: {animation.source}"
    for action in animation.actions:
        detail = ""
        if action.kind == "blur":
            detail = (f"intensity={action.blur_intensity} "
                      f"dir={action.blur_direction} type={action.blur_type}")
        else:
            detail = f"{action.start_value} -> {action.end_value}"
        yield (f"  {action.kind:9s} {action.start_time:.2f}-{action.end_time:.2f}  "
               f"ease={action.easing}  {detail}")
