"""Read-only calibrated touch gestures for the native 480x320 dashboard.

Opt-in only after operator-confirmed calibration. Does not open input devices,
configure the display, synthesize input or grant physical acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot, isfinite

from .calibration import Calibration
from .touch_nav import _EVENT, EV_ABS, EV_KEY, EV_SYN, ABS_X, ABS_Y, BTN_TOUCH

PAGES = ("overview", "captures", "doctor", "radios")
TAB_TOP = 296
TAB_WIDTH = 120
_SYN_REPORT = 0
_SYN_DROPPED = 3


@dataclass
class NavigationGestures:
    calibration: Calibration
    physically_verified: bool = False
    rotation: int = 0
    swipe_pixels: int = 60
    tap_slop_pixels: int = 18
    pressed: bool = False
    desynced: bool = False
    invalid: bool = False
    raw: dict = field(default_factory=lambda: {"x": None, "y": None})
    start: tuple | None = None
    end: tuple | None = None
    maximum_motion: float = 0.0

    def __post_init__(self):
        if self.physically_verified is not True:
            raise ValueError("physical touch calibration confirmation required")
        if not isinstance(self.calibration, Calibration):
            raise TypeError("measured calibration required")
        c = self.calibration
        if (c.width != 480 or c.height != 320 or
                type(c.maximum_error_px) not in (int, float) or
                not isfinite(c.maximum_error_px) or
                not 0 <= c.maximum_error_px <= 35):
            raise ValueError("unqualified display geometry or calibration")
        if self.rotation not in (0, 180):
            raise ValueError("unsupported display rotation")
        if (type(self.swipe_pixels) is not int or not 40 <= self.swipe_pixels <= 180 or
                type(self.tap_slop_pixels) is not int or not 4 <= self.tap_slop_pixels <= 30):
            raise ValueError("invalid gesture thresholds")

    def _clear(self):
        self.pressed = False
        self.invalid = False
        self.start = None
        self.end = None
        self.maximum_motion = 0.0
        self.raw = {"x": None, "y": None}

    def _point(self):
        x, y = self.raw["x"], self.raw["y"]
        if x is None or y is None:
            return None
        try:
            px, py = self.calibration.project(x, y)
        except (ValueError, TypeError, ZeroDivisionError, OverflowError):
            self.invalid = True
            return None
        if not all(isfinite(v) for v in (px, py)):
            self.invalid = True
            return None
        if not (0 <= px < 480 and 0 <= py < 320):
            self.invalid = True
            return None
        if self.rotation == 180:
            px, py = 479 - px, 319 - py
        return (px, py)

    def _sample(self):
        point = self._point()
        if point is None:
            return
        if self.start is None:
            self.start = point
        self.end = point
        self.maximum_motion = max(self.maximum_motion, hypot(
            point[0] - self.start[0], point[1] - self.start[1]))

    def _finish(self):
        if self.invalid or self.start is None or self.end is None:
            return None
        sx, sy = self.start
        ex, ey = self.end
        dx, dy = ex - sx, ey - sy
        if abs(dx) >= self.swipe_pixels and abs(dy) <= abs(dx) * 0.6:
            return ("swipe", 1 if dx < 0 else -1)
        # Vertical gestures inside the diagnostic content viewport change
        # the *view*, never any underlying device or Doctor state. A footer
        # drag or diagonal contact cannot become an accidental scroll.
        if (abs(dy) >= self.swipe_pixels and abs(dx) <= abs(dy) * 0.6
                and 155 <= sy < TAB_TOP and 155 <= ey < TAB_TOP):
            return ("scroll", 1 if dy < 0 else -1)
        if self.maximum_motion > self.tap_slop_pixels:
            return None
        if sy < TAB_TOP or ey < TAB_TOP:
            return None
        first, last = int(sx) // TAB_WIDTH, int(ex) // TAB_WIDTH
        if first != last or not 0 <= first < len(PAGES):
            return None
        return ("page", PAGES[first])

    def feed(self, event_type: int, code: int, value: int):
        """One complete evdev record; return a UI-only action or None."""
        if event_type == EV_SYN and code == _SYN_DROPPED:
            self._clear()
            self.desynced = True
            return None
        if self.desynced:
            if event_type == EV_SYN and code == _SYN_REPORT:
                self.desynced = False
            return None
        if event_type == EV_ABS:
            if code in (ABS_X, ABS_Y):
                self.raw["x" if code == ABS_X else "y"] = value
                if self.pressed:
                    self._sample()
            elif code in (47, 53, 54, 57) and self.pressed:
                # Unqualified MT slot/position streams are not classic touch.
                self.invalid = True
        elif event_type == EV_KEY and code == BTN_TOUCH:
            if value == 1 and not self.pressed:
                self.pressed = True
                self.start = self.end = None
                self.maximum_motion = 0.0
                self.invalid = False
                # Classic controllers may report ABS coordinates just before
                # BTN_TOUCH. Coordinates from old contacts were already cleared.
                self._sample()
            elif value == 0 and self.pressed:
                action = self._finish()
                self._clear()
                return action
            elif value not in (0, 1):
                self.invalid = True
        return None


def consume_gestures(buffer: bytes, tracker: NavigationGestures):
    """Consume bounded complete evdev records, carrying partial bytes forward."""
    if len(buffer) > 8192:
        raise ValueError("oversized input buffer")
    actions = []
    offset = 0
    while offset + _EVENT.size <= len(buffer):
        _, _, event_type, code, value = _EVENT.unpack_from(buffer, offset)
        offset += _EVENT.size
        action = tracker.feed(event_type, code, value)
        if action is not None:
            actions.append(action)
    return actions, buffer[offset:]
