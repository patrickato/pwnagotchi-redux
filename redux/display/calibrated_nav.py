"""Explicit, opt-in bridge from candidate calibration to classic swipe navigation.

This module does not read devices, enable touch, or attest physical accuracy.
"""
from __future__ import annotations

from .calibration import Calibration
from .touch_nav import SwipeTracker


def swipe_from_calibration(calibration: Calibration, *, physically_verified: bool,
                           minimum_pixels: int = 60) -> SwipeTracker:
    """Convert a physically confirmed mapping into existing raw-axis controls.

    A screen-left movement must always produce next-page navigation regardless
    of the XPT2046 wiring orientation or its rotated axes.
    """
    if physically_verified is not True:
        raise ValueError("physical screen and touch verification required")
    if not isinstance(calibration, Calibration):
        raise TypeError("a measured calibration is required")
    if type(minimum_pixels) is not int or not 20 <= minimum_pixels <= 200:
        raise ValueError("unsupported gesture distance")
    if calibration.width != 480 or calibration.height != 320:
        raise ValueError("only the qualified 480x320 geometry is supported")
    if calibration.maximum_error_px > 35:
        raise ValueError("calibration residual exceeds limit")
    raw_span = calibration.x_max - calibration.x_min
    if raw_span <= 0:
        raise ValueError("invalid raw axis span")
    threshold = round(minimum_pixels * raw_span / (calibration.width - 1))
    if not 20 <= threshold <= 8192:
        raise ValueError("raw swipe threshold outside supported bounds")
    return SwipeTracker(axis="y" if calibration.swap_axes else "x",
                        threshold=threshold, reverse=calibration.reverse_x)
