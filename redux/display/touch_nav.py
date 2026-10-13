"""Optional Linux evdev swipe navigation for the Redux native framebuffer UI.

Read-only classic single-touch (ADS7846/XPT2046) BTN_TOUCH + ABS_X/Y.
Never EVIOCGRAB, inject input, change calibration or open guessed eventN.
The operator must first identify the actual event node on their disposable Pi.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import stat
import struct

_INPUT = re.compile(r"/dev/input/event[0-9]{1,3}\Z")
# Linux input_event on 64-bit ARM64/AMD64: timeval (2 longs), type, code, value.
_EVENT = struct.Struct("llHHi")
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, BTN_TOUCH = 0, 1, 330


def open_touch(device, *, opener=os.open, checker=os.fstat, closer=os.close):
    """Return an explicitly selected nonblocking read-only event fd."""
    if not _INPUT.fullmatch(device):
        raise ValueError("select an explicit /dev/input/eventN")
    fd = opener(device, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if not stat.S_ISCHR(checker(fd).st_mode):
            raise ValueError("touch source is not an input character device")
        return fd
    except BaseException:
        closer(fd)
        raise


@dataclass
class SwipeTracker:
    axis: str = "x"
    threshold: int = 300
    reverse: bool = False
    pressed: bool = False
    start: int | None = None
    position: int | None = None

    def __post_init__(self):
        if self.axis not in ("x", "y") or not 20 <= self.threshold <= 8192:
            raise ValueError("unsupported touch axis or swipe threshold")

    def feed(self, type_code: int, code: int, value: int):
        """Return +1 next page, -1 previous page, or 0 (no complete swipe)."""
        active_axis = ABS_X if self.axis == "x" else ABS_Y
        if type_code == EV_ABS and code == active_axis:
            self.position = value
            if self.pressed and self.start is None:
                self.start = value
        elif type_code == EV_KEY and code == BTN_TOUCH:
            if value == 1 and not self.pressed:
                self.pressed = True
                # Discard the last finger's coordinates; wait for a fresh
                # axis event to establish this contact's start position.
                self.start = None
            elif value == 0 and self.pressed:
                self.pressed = False
                start, end = self.start, self.position
                self.start = None
                if start is None or end is None:
                    return 0
                delta = end - start
                if abs(delta) < self.threshold:
                    return 0
                # Left swipe -> next page; reverse handles rotated panels.
                direction = -1 if delta > 0 else 1
                return -direction if self.reverse else direction
        return 0


def consume(buffer: bytes, tracker: SwipeTracker):
    """Read bounded whole Linux input_event records; keep a trailing fragment."""
    events = []
    offset = 0
    while offset + _EVENT.size <= len(buffer):
        _, _, type_code, code, value = _EVENT.unpack_from(buffer, offset)
        offset += _EVENT.size
        if type_code == EV_SYN and code == 3:
            # SYN_DROPPED means the kernel dropped events; never produce a
            # gesture with stale start coordinates.
            tracker.pressed = False
            tracker.start = None
            tracker.position = None
        else:
            gesture = tracker.feed(type_code, code, value)
            if gesture:
                events.append(gesture)
    return events, buffer[offset:]


def navigate(page: str, step: int):
    pages = ("overview", "captures", "doctor")
    if page not in pages:
        raise ValueError("unknown page")
    return pages[(pages.index(page) + step) % len(pages)]
