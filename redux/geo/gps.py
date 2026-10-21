"""Live GPS position source (NMEA) — the missing piece that lights up geo.

Everything in geo/ (coverage grid, WiGLE lat/lon, centroid, GPX/KML export, the
moving map) needs a position fix, and Augur already accepts a `position_provider`
callable — but nothing ever supplied one and there was no GPS source at all. This
provides one: a pure NMEA parser (GGA + RMC, any talker, checksum-validated) and
a tracker that holds the latest fix, plus a thin serial reader that feeds it in
the background. A cheap USB GPS puck (e-waste-friendly) is all it takes.

Pure/testable: the parser and tracker do no I/O; the serial reader takes an
injected open-hook so the pump is exercised without hardware.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Tuple


@dataclass(frozen=True)
class GpsFix:
    lat: float
    lon: float


def _checksum_ok(line: str) -> bool:
    body, star, cks = line.partition("*")
    if not star:
        return True            # no checksum present — tolerate (some sources omit)
    calc = 0
    for ch in body.lstrip("$"):
        calc ^= ord(ch)
    try:
        return calc == int(cks.strip()[:2], 16)
    except ValueError:
        return False


def _coord(val: str, hemi: str) -> Optional[float]:
    """NMEA ddmm.mmmm / dddmm.mmmm + hemisphere -> signed decimal degrees."""
    if not val:
        return None
    try:
        v = float(val)
    except ValueError:
        return None
    deg = int(v // 100)
    dec = deg + (v - deg * 100) / 60.0
    if hemi in ("S", "W"):
        dec = -dec
    return dec


def parse_nmea_line(line: str) -> Optional[GpsFix]:
    """One NMEA sentence -> GpsFix, or None (no fix / invalid / unsupported type).

    Handles GGA (uses fix-quality) and RMC (uses A/V status), any talker id
    (GP/GN/GL/...). A present-but-wrong checksum rejects the line."""
    line = (line or "").strip()
    if not line.startswith("$") or not _checksum_ok(line):
        return None
    f = line.split("*", 1)[0].split(",")
    if len(f) < 7:
        return None
    typ = f[0][-3:]
    if typ == "GGA":
        if f[6] in ("", "0"):          # fix quality 0 = no fix
            return None
        lat, lon = _coord(f[2], f[3]), _coord(f[4], f[5])
    elif typ == "RMC":
        if f[2] != "A":                # status A = valid, V = void
            return None
        lat, lon = _coord(f[3], f[4]), _coord(f[5], f[6])
    else:
        return None
    if lat is None or lon is None or not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return GpsFix(lat, lon)


class NmeaTracker:
    """Holds the most recent valid fix; usable directly as a position_provider."""
    def __init__(self) -> None:
        self._fix: Optional[GpsFix] = None

    def feed(self, line: str) -> Optional[GpsFix]:
        fix = parse_nmea_line(line)
        if fix is not None:
            self._fix = fix
        return self._fix

    @property
    def fix(self) -> Optional[GpsFix]:
        return self._fix

    def __call__(self) -> Optional[Tuple[float, float]]:
        return (self._fix.lat, self._fix.lon) if self._fix is not None else None


def pump_lines(lines: Iterable[str], tracker: NmeaTracker, *,
               max_lines: Optional[int] = None) -> NmeaTracker:
    """Feed NMEA lines into a tracker (pure; the testable core of the reader)."""
    n = 0
    for raw in lines:
        line = raw.decode("ascii", "replace") if isinstance(raw, (bytes, bytearray)) else raw
        tracker.feed(line)
        n += 1
        if max_lines is not None and n >= max_lines:
            break
    return tracker


def serial_position_provider(device: str = "/dev/ttyUSB0", baud: int = 9600, *,
                             open_stream: Optional[Callable[[str, int], Iterable]] = None
                             ) -> NmeaTracker:
    """Background-read NMEA from a serial GPS and expose the latest fix as a
    position_provider callable. Best-effort: returns None until a fix arrives, and
    stays silent (never raises) if the device can't be opened. `open_stream` is
    injected in tests; on device it defaults to a pyserial line stream."""
    import threading
    tracker = NmeaTracker()

    def _default_open(dev: str, b: int):
        import serial  # pyserial, present only on device
        return serial.Serial(dev, b, timeout=1)

    opener = open_stream or _default_open

    def _reader() -> None:
        try:
            stream = opener(device, baud)
        except Exception:
            return                     # no GPS / no pyserial — stay at None, quietly
        try:
            for raw in stream:
                tracker.feed(raw.decode("ascii", "replace")
                             if isinstance(raw, (bytes, bytearray)) else raw)
        except Exception:
            return

    threading.Thread(target=_reader, daemon=True).start()
    return tracker
