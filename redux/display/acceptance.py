"""Read-only evidence collection for Redux 480x320 screen and XPT2046 trials.

This module does not configure Xorg, read framebuffer pixels, grab input,
change calibration, or certify hardware. A physical operator still has to
verify the screen and all touch targets on a separate development SD card.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import select
import shutil
import stat
import struct
import subprocess
import time

from . import kiosk

_EVENT = re.compile(r"/dev/input/event[0-9]{1,3}\Z")
_FORMAT = struct.Struct("@llHHi")  # Linux input_event; native long is 32/64-bit.
_EV_SYN, _EV_KEY, _EV_ABS = 0, 1, 3
_SYN_REPORT, _SYN_DROPPED = 0, 3
_BTN_TOUCH, _ABS_X, _ABS_Y = 330, 0, 1


def _read(path: Path, limit=256) -> str | None:
    """Read at most a small sysfs attribute; never load unbounded data."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit)
        return raw.decode("utf-8", "replace").strip("\x00\r\n ")
    except OSError:
        return None


def _driver_name(path: Path) -> str | None:
    try:
        return path.resolve(strict=True).name if path.is_symlink() else None
    except OSError:
        return None


def inventory(*, graphics=Path("/sys/class/graphics"),
              inputs=Path("/sys/class/input"),
              spi=Path("/sys/bus/spi/devices")) -> dict:
    """Report kernel-exposed framebuffers and input devices, never guess fbN."""
    screens = []
    graphics_nodes=sorted(graphics.glob("fb[0-9]*"), key=lambda p: p.name)
    input_nodes=sorted(inputs.glob("event[0-9]*"), key=lambda p: p.name)
    spi_nodes=sorted(spi.glob("spi*"), key=lambda p: p.name)
    truncated=len(graphics_nodes)>64 or len(input_nodes)>64 or len(spi_nodes)>32
    for fb in graphics_nodes[:64]:
        if not re.fullmatch(r"fb[0-9]+", fb.name):
            continue
        screens.append({"device": f"/dev/{fb.name}",
                        "name": _read(fb / "name"),
                        "virtual_size": _read(fb / "virtual_size"),
                        "bits_per_pixel": _read(fb / "bits_per_pixel"),
                        "stride": _read(fb / "stride"),
                        "driver": _driver_name(fb / "device" / "driver")})
    devices = []
    for event in input_nodes[:64]:
        if not re.fullmatch(r"event[0-9]+", event.name):
            continue
        node = event / "device"
        devices.append({"device": f"/dev/input/{event.name}",
                        "name": _read(node / "name"),
                        "abs_bits": _read(node / "capabilities/abs"),
                        "key_bits": _read(node / "capabilities/key"),
                        "properties": _read(node / "properties"),
                        "modalias": _read(node / "modalias", 128),
                        "compatible": (_read(node / "of_node" / "compatible", 128)
                                       or "").replace("\x00", " ").strip() or None,
                        "driver": _driver_name(node / "driver")})
    # SPI evidence is independent of the framebuffer. A bound ILI9486
    # controller can exist without a usable fbN and vice versa; never infer
    # physically visible pixels from a device-tree compatible string.
    spi_devices = []
    for node in spi_nodes[:32]:
        if not re.fullmatch(r"spi[0-9]{1,3}\.[0-9]{1,3}", node.name):
            continue
        driver = node / "driver"
        spi_devices.append({
            "sysfs_device": node.name,
            "modalias": _read(node / "modalias", 128),
            "compatible": (_read(node / "of_node" / "compatible", 128) or "").replace("\x00", " ").strip() or None,
            "driver": _driver_name(driver),
            "device_tree_status": _read(node / "of_node" / "status", 64),
        })
    return {"framebuffers": screens, "input_devices": devices,
            "spi_devices": spi_devices, "inventory_truncated": truncated}


def panel_readiness(overview: dict) -> dict:
    """Classify metadata needed to select a future physical 480x320 test.

    This never promotes sysfs names to visible-pixel or working-touch proof.
    Multiple plausible devices are ambiguous and require operator selection.
    """
    if not isinstance(overview, dict):
        return {"state": "INVALID", "blockers": ["INVENTORY INVALID"],
                "physical_display_verified": False, "touch_verified": False}
    framebuffers = overview.get("framebuffers")
    inputs = overview.get("input_devices")
    spi = overview.get("spi_devices")
    if not all(isinstance(value, list) for value in (framebuffers, inputs, spi)):
        return {"state": "INVALID", "blockers": ["INVENTORY INVALID"],
                "physical_display_verified": False, "touch_verified": False}
    def label(row,key):
        value=row.get(key)
        return value.lower() if isinstance(value,str)and len(value)<=256 else""
    fb = [row for row in framebuffers if isinstance(row, dict)
          and row.get("virtual_size") == "480,320"
          and row.get("bits_per_pixel") == "16" and row.get("stride") == "960"]
    controllers = [row for row in spi if isinstance(row, dict)
                   and "ili9486" in " ".join(label(row,key)
                                                for key in ("modalias", "compatible"))]
    touch = [row for row in inputs if isinstance(row, dict)
             and any(kind in " ".join(label(row,key)
                                      for key in ("name", "modalias", "compatible"))
                     for kind in ("ads7846", "xpt2046"))]
    blockers=[]
    if overview.get("inventory_truncated") is True:blockers.append("INVENTORY TRUNCATED")
    if len(fb)!=1:blockers.append("FRAMEBUFFER MISSING"if not fb else"FRAMEBUFFER AMBIGUOUS")
    if len(controllers)!=1:blockers.append("ILI9486 MISSING"if not controllers else"ILI9486 AMBIGUOUS")
    elif not controllers[0].get("driver"):blockers.append("ILI9486 DRIVER UNBOUND")
    if len(touch)!=1:blockers.append("TOUCH DEVICE MISSING"if not touch else"TOUCH DEVICE AMBIGUOUS")
    elif not touch[0].get("abs_bits")or not touch[0].get("key_bits"):
        blockers.append("TOUCH CAPABILITIES MISSING")
    state="READY_FOR_OPERATOR_SELECTION"if not blockers else"BLOCKED"
    return {"state":state,"blockers":blockers,
            "framebuffer_candidates":len(fb),"ili9486_candidates":len(controllers),
            "touch_candidates":len(touch),"physical_display_verified":False,
            "touch_verified":False}


def xinput_probe(display: str, *, which=shutil.which,
                 runner=subprocess.run) -> dict:
    """Ask authenticated local X11 for devices; does not establish mapping."""
    result = {"ok": False, "devices": [], "reason": ""}
    if not re.fullmatch(r":[0-9]{1,3}(?:\.[0-9]{1,2})?", display or ""):
        result["reason"] = "No local X11 DISPLAY; touch mapping not assessed"
        return result
    binary = which("xinput")
    if not binary or not os.path.isabs(binary):
        result["reason"] = "xinput unavailable (optional display diagnostic package)"
        return result
    try:
        proc = runner([binary, "--display", display, "list", "--short"],
                      capture_output=True, text=True, timeout=3, check=False)
        if proc.returncode:
            result["reason"] = "xinput could not query this authenticated X11 session"
            return result
        lines = [line.strip()[:180] for line in proc.stdout[:8192].splitlines()]
        result["devices"] = lines[:48]
        result["ok"] = True
        result["reason"] = "X11 advertised these devices; calibration unverified"
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        result["reason"] = f"xinput unavailable: {type(exc).__name__}"
    return result


def abs_bounds(fd: int, axis: int, *, ioctl=fcntl.ioctl) -> dict | None:
    """Query Linux EVIOCGABS; only used on an explicitly selected event node."""
    data = bytearray(24)  # struct input_absinfo: 6 signed 32-bit integers
    try:
        ioctl(fd, 0x80184540 + axis, data, True)
        _, minimum, maximum, _, _, _ = struct.unpack("=6i", data)
        if minimum >= maximum:
            return None
        return {"min": minimum, "max": maximum}
    except OSError:
        return None


def _percentage(value: int, bounds: dict | None) -> float | None:
    if not bounds or not bounds["min"] <= value <= bounds["max"]:
        return None
    return round(100 * (value - bounds["min"]) /
                 (bounds["max"] - bounds["min"]), 2)


class TapDecoder:
    """Decode only classic single-touch ABS_X/ABS_Y + BTN_TOUCH releases.

    Multitouch slots, X11 pointer transforms and unsynchronised input must be
    assessed separately. We deliberately do not claim those are working.
    """
    def __init__(self, x_bounds=None, y_bounds=None):
        self.axes = {"x": None, "y": None}
        self.bounds = {"x": x_bounds, "y": y_bounds}
        self.down = False
        self.desynced = False
        self.taps: list[dict] = []
        self.partial = bytearray()
        self.dropped = 0

    def feed(self, data: bytes):
        self.partial.extend(data)
        if len(self.partial) > 16384:
            raise ValueError("input event buffer exceeded safe bound")
        while len(self.partial) >= _FORMAT.size:
            raw = self.partial[:_FORMAT.size]
            del self.partial[:_FORMAT.size]
            _, _, kind, code, value = _FORMAT.unpack(raw)
            if kind == _EV_SYN and code == _SYN_DROPPED:
                self.desynced = True
                self.down = False
                self.axes = {"x": None, "y": None}
                self.dropped += 1
            elif kind == _EV_SYN and code == _SYN_REPORT:
                self.desynced = False
            elif self.desynced:
                continue
            elif kind == _EV_ABS and code in (_ABS_X, _ABS_Y):
                self.axes["x" if code == _ABS_X else "y"] = value
            elif kind == _EV_KEY and code == _BTN_TOUCH:
                if value == 1:
                    self.down = True
                elif value == 0 and self.down:
                    self.down = False
                    if all(v is not None for v in self.axes.values()):
                        x, y = self.axes["x"], self.axes["y"]
                        self.taps.append({"raw_x": x, "raw_y": y,
                                          "x_percent": _percentage(x, self.bounds["x"]),
                                          "y_percent": _percentage(y, self.bounds["y"])})
                    # A subsequent contact must report its own ABS_X and ABS_Y.
                    # Otherwise a second BTN_TOUCH pair would be incorrectly
                    # counted as another tap at the previous finger's location.
                    # Reset on release (not press): some controllers send their
                    # initial ABS coordinates immediately before BTN_TOUCH.
                    self.axes = {"x": None, "y": None}


def sample_touch(device: str, *, seconds=12, max_taps=12,
                 opener=os.open, reader=os.read, selector=select.select,
                 clock=time.monotonic, closer=os.close) -> dict:
    """Opt-in, non-grabbing, read-only kernel event sample. Never sends events."""
    if not _EVENT.fullmatch(device):
        raise ValueError("explicit /dev/input/eventN device required")
    if not 1 <= seconds <= 60 or not 1 <= max_taps <= 30:
        raise ValueError("touch sampling limits are 1..60 s and 1..30 taps")
    outcome = {"device": device, "status": "not_assessed", "taps": [],
               "dropped_syncs": 0, "raw_axis_bounds": {},
               "physical_touch_verified": False,
               "reason": "Raw event observation does not verify X11 mapping or pixels"}
    fd = None
    try:
        fd = opener(device, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            raise ValueError("selected event path is not a character device")
        x_bounds = abs_bounds(fd, _ABS_X)
        y_bounds = abs_bounds(fd, _ABS_Y)
        outcome["raw_axis_bounds"] = {"x": x_bounds, "y": y_bounds}
        parser = TapDecoder(x_bounds, y_bounds)
        deadline = clock() + seconds
        while len(parser.taps) < max_taps:
            remaining = deadline - clock()
            if remaining <= 0:
                break
            readable, _, _ = selector([fd], [], [], min(remaining, 0.25))
            if readable:
                try:
                    block = reader(fd, 4096)
                except BlockingIOError:
                    continue
                if not block:
                    break
                parser.feed(block)
        outcome["taps"] = parser.taps[:max_taps]
        outcome["dropped_syncs"] = parser.dropped
        outcome["status"] = "observed" if outcome["taps"] else "no_complete_taps"
    except (OSError, ValueError) as exc:
        outcome["status"] = "error"
        outcome["reason"] = f"Touch read failed: {type(exc).__name__}: {exc}"
    finally:
        if fd is not None:
            closer(fd)
    return outcome


def report(*, framebuffer=None, touch_device=None, seconds=12,
           graphics=Path("/sys/class/graphics"),
           inputs=Path("/sys/class/input"),
           spi=Path("/sys/bus/spi/devices"), environ=None, uid=None) -> dict:
    """Snapshot truthfully separated into observed and unassessed evidence."""
    env = os.environ if environ is None else environ
    effective_uid = os.geteuid() if uid is None else uid
    overview = inventory(graphics=graphics, inputs=inputs, spi=spi)
    result = {"schema": 1, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "framebuffer_candidates": overview["framebuffers"],
              "input_candidates": overview["input_devices"],
              "spi_candidates": overview["spi_devices"],
              "inventory_truncated": overview["inventory_truncated"],
              "panel_readiness": panel_readiness(overview),
              "selected_framebuffer": None, "x11": None, "x11_geometry": None,
              "xinput": None, "touch_sample": None,
              "physical_display_verified": False, "touch_verified": False,
              "hardware_acceptance": "pending_operator_physical_tests"}
    if framebuffer:
        result["selected_framebuffer"] = kiosk.framebuffer_probe(
            framebuffer, sys_graphics=graphics)
    display = env.get("DISPLAY", "")
    if display and effective_uid != 0:
        result["x11"] = kiosk.x11_probe(display)
        if result["x11"]["ok"]:
            result["x11_geometry"] = kiosk.x11_geometry_probe(display)
            result["xinput"] = xinput_probe(display)
    if touch_device:
        result["touch_sample"] = sample_touch(touch_device, seconds=seconds)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Redux read-only TFT/touch acceptance evidence")
    parser.add_argument("--framebuffer", help="actual /dev/fbN; optional for discovery")
    parser.add_argument("--touch-device", help="explicit /dev/input/eventN; capture raw taps read-only")
    parser.add_argument("--seconds", type=int, default=12, help="touch sampling seconds (1..60)")
    args = parser.parse_args(argv)
    if args.framebuffer and not kiosk._FB.fullmatch(args.framebuffer):
        parser.error("--framebuffer must be /dev/fbN")
    if args.touch_device and not _EVENT.fullmatch(args.touch_device):
        parser.error("--touch-device must be /dev/input/eventN")
    if not 1 <= args.seconds <= 60:
        parser.error("--seconds must be 1..60")
    result = report(framebuffer=args.framebuffer, touch_device=args.touch_device,
                    seconds=args.seconds)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.framebuffer and not result["selected_framebuffer"]["ok"]:
        return 2
    if args.touch_device and result["touch_sample"]["status"] == "error":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
