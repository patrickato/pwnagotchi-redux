"""gpsd client — the standard way to get a position on Linux.

`gps.py` reads NMEA straight off a serial device. gpsd is the daemon most people
actually run: it owns the GPS (USB/serial/Bluetooth), and every client reads
position from it over TCP (localhost:2947) as line-delimited JSON. Supporting
gpsd means Augur gets a fix no matter how the GPS is attached, and plays nice
with anything else on the box that wants the GPS too.

Protocol: connect, send ``?WATCH={"enable":true,"json":true}``, then read JSON
objects. A ``TPV`` (time-position-velocity) report carries ``mode`` (2=2D fix,
3=3D), ``lat``, ``lon``. We only trust a report with a real fix (mode>=2) and
finite, in-range coordinates — a mode-0/1 report has no position and is skipped
(never guessed).

Pure/testable: the client reads from an injected line source, so the whole parse
+ provider flow is exercised with recorded gpsd output and no socket.
"""
from __future__ import annotations

import json
import math
from typing import Callable, Iterable, Iterator, Optional, Tuple

from .gps import GpsFix


def parse_tpv(obj: dict) -> Optional[GpsFix]:
    """A gpsd JSON object → GpsFix, or None if it isn't a usable position fix."""
    if not isinstance(obj, dict) or obj.get("class") != "TPV":
        return None
    try:
        mode = int(obj.get("mode", 0))
    except (TypeError, ValueError):
        mode = 0
    if mode < 2:                       # 0=unknown, 1=no fix -> no position
        return None
    lat, lon = obj.get("lat"), obj.get("lon")
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return GpsFix(lat=lat, lon=lon)


def fixes_from_lines(lines: Iterable[str]) -> Iterator[GpsFix]:
    """Yield a GpsFix for every usable TPV in a stream of gpsd JSON lines."""
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        fix = parse_tpv(obj)
        if fix is not None:
            yield fix


_WATCH = b'?WATCH={"enable":true,"json":true}\n'


def _socket_lines(host: str, port: int, timeout: float) -> Iterator[str]:
    """Default line source: connect to gpsd, enable JSON, yield lines."""
    import socket
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.sendall(_WATCH)
        buf = b""
        sock.settimeout(timeout)
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                yield line.decode("utf-8", "replace")
    finally:
        try:
            sock.close()
        except OSError:
            pass


class GpsdClient:
    """Reads fixes from gpsd. `line_source(host, port, timeout) -> iterator of
    JSON lines` is injectable, so tests feed recorded output with no socket."""

    def __init__(self, host: str = "127.0.0.1", port: int = 2947, *,
                 timeout: float = 5.0,
                 line_source: Callable[[str, int, float], Iterable[str]] = _socket_lines):
        self.host, self.port, self.timeout = host, port, timeout
        self._line_source = line_source

    def poll_fix(self) -> Optional[GpsFix]:
        """The first usable fix from gpsd, or None (no fix / unreachable)."""
        try:
            for fix in fixes_from_lines(self._line_source(self.host, self.port, self.timeout)):
                return fix
        except OSError:
            return None
        return None


def gpsd_position_provider(host: str = "127.0.0.1", port: int = 2947, *,
                           timeout: float = 5.0,
                           line_source: Callable = _socket_lines
                           ) -> Callable[[], Optional[Tuple[float, float]]]:
    """A position provider (callable -> (lat, lon) | None) backed by gpsd, for
    Augur's position_provider slot. Fails safe to None when gpsd is down."""
    client = GpsdClient(host, port, timeout=timeout, line_source=line_source)

    def _provider() -> Optional[Tuple[float, float]]:
        fix = client.poll_fix()
        return (fix.lat, fix.lon) if fix is not None else None

    return _provider
