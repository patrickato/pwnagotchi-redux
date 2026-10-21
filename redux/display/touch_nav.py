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
    desynced: bool = False

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
            # Kernel input stream lost events. Until the next SYN_REPORT the
            # intervening records are unsynchronised and cannot form a swipe.
            tracker.desynced = True
            tracker.pressed = False
            tracker.start = None
            tracker.position = None
        elif tracker.desynced:
            if type_code == EV_SYN and code == 0:
                tracker.desynced = False
            # The subsequent contact must supply its own touch/axis records.
        else:
            gesture = tracker.feed(type_code, code, value)
            if gesture:
                events.append(gesture)
    return events, buffer[offset:]


def navigate(page: str, step: int):
    from .gesture_nav import PAGES as pages
    if page not in pages:
        raise ValueError("unknown page")
    return pages[(pages.index(page) + step) % len(pages)]


def touch_identity(fd):
    """Read-only kernel identity plus physical input path; no guessed nodes."""
    import fcntl
    identity = bytearray(8)
    physical = bytearray(256)
    fcntl.ioctl(fd, 0x80084502, identity, True)  # EVIOCGID
    fcntl.ioctl(fd, 0x81004507, physical, True)  # EVIOCGPHYS(256)
    path = bytes(physical).split(b'\0', 1)[0]
    if not path or not any(identity):
        raise OSError('input identity unavailable')
    return bytes(identity), path


class TouchReconnect:
    """Bounded reopening of the operator-selected node with identity continuity.

    Failed identity measurement disables automatic reopening. A different input
    device never inherits the previous controller's calibration.
    """
    def __init__(self, device, fd, *, identity=touch_identity, opener=open_touch,
                 closer=os.close, clock=None):
        import time
        self.device, self.fd = device, fd
        self.identity, self.opener, self.closer = identity, opener, closer
        self.clock = clock or time.monotonic
        self.expected = None
        self.next_try = float('-inf')
        self.state = 'connected'
        try:
            self.expected = identity(fd)
        except OSError:
            self.state = 'connected / identity unverified'

    def disconnect(self):
        if self.fd is not None:
            try:
                self.closer(self.fd)
            except OSError:
                pass
        self.fd = None
        self.state = 'disconnected'
        self.next_try = self.clock() + 5

    def poll(self):
        if self.fd is not None or self.expected is None or self.clock() < self.next_try:
            return None
        self.next_try = self.clock() + 5
        candidate = None
        try:
            candidate = self.opener(self.device)
            if self.identity(candidate) != self.expected:
                self.state = 'identity mismatch / restart after verification'
                return None
            self.fd, candidate = candidate, None
            self.state = 'connected'
            return self.fd
        except OSError:
            self.state = 'disconnected'
            return None
        finally:
            if candidate is not None:
                self.closer(candidate)

    def close(self):
        self.disconnect()
