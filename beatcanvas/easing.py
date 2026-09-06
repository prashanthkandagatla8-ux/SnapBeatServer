"""Cubic bezier easing, ported exactly from CapCut's ``Transform.lua``.

CapCut's animation bundles evaluate easing with a cubic bezier whose end points are
fixed at (0,0) and (1,1), controlled by two interior points. That is the same形 as a CSS
``cubic-bezier``, so the curves convert with no approximation:

    x(t) = 3·xc1·(1-t)²·t + 3·xc2·(1-t)·t² + t³
    y(t) = 3·yc1·(1-t)²·t + 3·yc2·(1-t)·t² + t³

Progress is *not* ``y(t)`` at ``t = time``. The curve is parametric, so the parameter
``t`` must first be solved from the horizontal coordinate, then ``y`` read off. CapCut
does that with a bisection search; the same method is used here so results agree.
"""
from __future__ import annotations

from typing import Sequence

#: A bezier easing is four numbers: the two interior control points, (x1,y1,x2,y2).
Controls = Sequence[float]

LINEAR: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)


def bezier_value(controls: Controls, t: float) -> tuple[float, float]:
    """Point on the curve at parameter ``t``."""
    xc1, yc1, xc2, yc2 = controls
    one_minus = 1.0 - t
    x = 3.0 * xc1 * one_minus * one_minus * t + 3.0 * xc2 * one_minus * t * t + t ** 3
    y = 3.0 * yc1 * one_minus * one_minus * t + 3.0 * yc2 * one_minus * t * t + t ** 3
    return x, y


def bezier_derivative(controls: Controls, t: float) -> tuple[float, float]:
    """Tangent of the curve at parameter ``t``."""
    xc1, yc1, xc2, yc2 = controls
    one_minus = 1.0 - t
    dx = 3.0 * xc1 * one_minus * (1.0 - 3.0 * t) + 3.0 * xc2 * (2.0 - 3.0 * t) * t \
        + 3.0 * t * t
    dy = 3.0 * yc1 * one_minus * (1.0 - 3.0 * t) + 3.0 * yc2 * (2.0 - 3.0 * t) * t \
        + 3.0 * t * t
    return dx, dy


def bezier_t_from_x(controls: Controls, x: float, tolerance: float = 1e-4) -> float:
    """Solve the curve parameter that produces horizontal coordinate ``x``.

    Bisection, matching CapCut's own loop and its 1e-4 termination, so the easing lands
    on the same values rather than merely a similar shape.
    """
    low, high = 0.0, 1.0
    while high - low >= tolerance:
        mid = (low + high) * 0.5
        if bezier_value(controls, mid)[0] > x:
            high = mid
        else:
            low = mid
    return (low + high) * 0.5


def ease(controls: Controls, progress: float) -> float:
    """Eased progress in 0..1 for linear input progress in 0..1."""
    if progress <= 0.0:
        return 0.0
    if progress >= 1.0:
        return 1.0
    if tuple(controls) == LINEAR:
        return progress
    t = bezier_t_from_x(controls, progress)
    return bezier_value(controls, t)[1]


def ease_velocity(controls: Controls, progress: float) -> float:
    """Rate of change of eased progress, i.e. |dy/dx| at this point on the curve.

    CapCut uses this to drive motion blur, so fast parts of a move smear more than slow
    parts. Without it the blur is uniform and the motion loses its snap.
    """
    progress = min(1.0, max(0.0, progress))
    t = bezier_t_from_x(controls, progress)
    dx, dy = bezier_derivative(controls, t)
    if abs(dx) < 1e-6:
        return 0.0
    return abs(dy / dx)


def lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * amount
