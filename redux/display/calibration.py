"""Pure touchscreen calibration candidate evaluation; never programs an input device.

Candidates must be confirmed on the physical panel before use.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class TouchPoint:
    raw_x: float
    raw_y: float
    pixel_x: float
    pixel_y: float


@dataclass(frozen=True)
class Calibration:
    swap_axes: bool
    reverse_x: bool
    reverse_y: bool
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    width: int
    height: int
    maximum_error_px: float

    def project(self, raw_x: float, raw_y: float) -> tuple[float, float]:
        if not all(isfinite(v) for v in (raw_x, raw_y)):
            raise ValueError("non-finite touch coordinate")
        x, y = (raw_y, raw_x) if self.swap_axes else (raw_x, raw_y)
        x = (x - self.x_min) / (self.x_max - self.x_min)
        y = (y - self.y_min) / (self.y_max - self.y_min)
        if self.reverse_x:
            x = 1 - x
        if self.reverse_y:
            y = 1 - y
        return (x * (self.width - 1), y * (self.height - 1))


def evaluate(points: list[TouchPoint], *, width: int = 480, height: int = 320,
             tolerance_px: float = 35) -> Calibration | None:
    """Fit eight axis-aligned/swap/reverse candidates to labeled physical taps.

    Requires four corners and a center. An ambiguous or inconsistent fit fails
    closed, and never implies that physical mapping has been accepted.
    """
    if (type(width) is not int or type(height) is not int
            or width < 2 or height < 2 or len(points) < 5 or len(points) > 32
            or type(tolerance_px) not in (int, float)
            or not isfinite(tolerance_px) or not 0 < tolerance_px <= 80):
        return None
    vals = [(p.raw_x, p.raw_y, p.pixel_x, p.pixel_y) for p in points]
    if not all(all(type(v) in (int, float) and isfinite(v) for v in row) for row in vals):
        return None
    if not all(0 <= p.pixel_x < width and 0 <= p.pixel_y < height for p in points):
        return None
    targets = [(0, 0), (width - 1, 0), (0, height - 1),
               (width - 1, height - 1), ((width - 1) / 2, (height - 1) / 2)]
    # Distinct targets need distinct samples. Otherwise one center-ish sample
    # could satisfy multiple targets on small panels or an uneven test set.
    possible = [tuple(i for i, p in enumerate(points)
                      if abs(p.pixel_x - tx) <= width * .2
                      and abs(p.pixel_y - ty) <= height * .2)
                for tx, ty in targets]
    if any(not indices for indices in possible):
        return None
    def has_matching(target=0, used=frozenset()):
        if target == len(possible):
            return True
        return any(i not in used and has_matching(target + 1, used | {i})
                   for i in possible[target])
    if not has_matching():
        return None
    fits = []
    for swap in (False, True):
        xs = [p.raw_y if swap else p.raw_x for p in points]
        ys = [p.raw_x if swap else p.raw_y for p in points]
        lowx, highx = min(xs), max(xs)
        lowy, highy = min(ys), max(ys)
        if highx - lowx < 50 or highy - lowy < 50:
            continue
        for rx in (False, True):
            for ry in (False, True):
                candidate = Calibration(swap, rx, ry, lowx, highx, lowy, highy,
                                        width, height, 0)
                errors = [max(abs(candidate.project(p.raw_x, p.raw_y)[0] - p.pixel_x),
                              abs(candidate.project(p.raw_x, p.raw_y)[1] - p.pixel_y))
                          for p in points]
                fits.append((max(errors), candidate))
    if not fits:
        return None
    fits.sort(key=lambda pair: pair[0])
    if fits[0][0] > tolerance_px:
        return None
    if len(fits) > 1 and fits[1][0] - fits[0][0] < 5:
        return None
    error, winner = fits[0]
    return Calibration(winner.swap_axes, winner.reverse_x, winner.reverse_y,
                       winner.x_min, winner.x_max, winner.y_min, winner.y_max,
                       width, height, round(error, 2))
