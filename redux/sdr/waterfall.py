"""Waterfall rendering from rtl_power sweeps — making the spectrum *visible*.

`spectrum.py` reduces a sweep to peaks + a noise floor (great for "what's on the
air", but it throws away the time dimension). A **waterfall** keeps time: each
sweep becomes a row, stacked newest-last, so you can *see* a signal come and go
across a band.

This is tier 1 of the waterfall: a pure model that turns an `rtl_power` CSV
(which repeats a sweep over time) into a time × frequency intensity grid, and
renders it two dependency-free ways:

* **ASCII** — a text waterfall that shows up anywhere text does: a terminal,
  the TFT's text pages, or a web ``<pre>``. Viewable *now*.
* **PGM** — a standard grayscale image (P5), written in pure Python, that any
  image viewer / the web ``<img>`` can show.

What this is NOT (yet): a real-time stream pushed live to web+phone+TFT at once.
That needs a continuous rtl_power producer + an SSE/websocket transport — a later
tier. This turns the data we already have into a real picture, honestly, and is
fully testable with no radio.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# intensity ramp, faint -> strong (for ASCII rendering)
_RAMP = " .:-=+*#%@"


@dataclass(frozen=True)
class WaterfallRow:
    ts_key: str                 # the sweep's timestamp label (date+time)
    freqs_hz: Tuple[float, ...]
    db: Tuple[float, ...]


def parse_rtl_power_rows(lines) -> List[WaterfallRow]:
    """Group rtl_power CSV lines into one row per sweep timestamp (preserving
    the time dimension, unlike spectrum.parse_rtl_power which flattens)."""
    groups: Dict[str, List[Tuple[float, float]]] = {}
    order: List[str] = []
    for raw in lines:
        parts = [p.strip() for p in (raw or "").split(",")]
        if len(parts) < 7:
            continue
        try:
            hz_low = float(parts[2]); hz_high = float(parts[3]); hz_step = float(parts[4])
        except ValueError:
            continue
        if hz_step <= 0:
            continue
        key = f"{parts[0]} {parts[1]}"
        if key not in groups:
            order.append(key)
        bins = groups.setdefault(key, [])
        for i, p in enumerate(parts[6:]):
            try:
                v = float(p)
            except ValueError:
                continue
            if not math.isfinite(v):
                continue
            f = hz_low + i * hz_step
            if f > hz_high + hz_step:
                break
            bins.append((f, v))
    rows: List[WaterfallRow] = []
    for key in order:
        bins = sorted(groups.get(key, []), key=lambda t: t[0])
        if bins:
            rows.append(WaterfallRow(key, tuple(f for f, _ in bins), tuple(d for _, d in bins)))
    return rows


@dataclass(frozen=True)
class Waterfall:
    rows: Tuple[WaterfallRow, ...]
    db_min: float
    db_max: float

    @classmethod
    def from_rtl_power(cls, lines) -> "Waterfall":
        rows = parse_rtl_power_rows(lines)
        allv = [v for r in rows for v in r.db]
        lo = min(allv) if allv else 0.0
        hi = max(allv) if allv else 1.0
        if hi <= lo:
            hi = lo + 1.0
        return cls(tuple(rows), lo, hi)

    @property
    def shape(self) -> Tuple[int, int]:
        return (len(self.rows), len(self.rows[0].db) if self.rows else 0)

    def _norm(self, v: float) -> float:
        return max(0.0, min(1.0, (v - self.db_min) / (self.db_max - self.db_min)))

    def _resample_row(self, row: WaterfallRow, width: int) -> List[float]:
        """Downsample/collapse a row's bins to `width` columns by max-in-bucket
        (so a narrow strong signal isn't averaged away)."""
        n = len(row.db)
        if n == 0 or width <= 0:
            return []
        if n <= width:
            return [self._norm(v) for v in row.db]
        out = []
        for c in range(width):
            a = c * n // width
            b = max(a + 1, (c + 1) * n // width)
            out.append(self._norm(max(row.db[a:b])))
        return out

    def to_ascii(self, *, width: int = 64) -> str:
        """A text waterfall: one line per sweep, newest last. Shows up anywhere
        text does (terminal, TFT text page, web <pre>)."""
        if not self.rows:
            return "(empty waterfall — no sweeps)"
        lines = []
        for r in self.rows:
            cols = self._resample_row(r, width)
            line = "".join(_RAMP[min(len(_RAMP) - 1, int(i * (len(_RAMP) - 1) + 0.5))] for i in cols)
            lines.append(line)
        lo = self.rows[0].freqs_hz[0] / 1e6
        hi = self.rows[0].freqs_hz[-1] / 1e6
        header = f"{lo:.3f} MHz{'':<{max(0, width - 20)}}{hi:.3f} MHz"
        return header + "\n" + "\n".join(lines)

    def to_pgm(self, *, width: Optional[int] = None) -> bytes:
        """A grayscale PGM (P5) image: rows=time, cols=frequency, value=intensity.
        Pure Python, no deps; viewable in any image viewer or a web <img>."""
        if not self.rows:
            return b"P5\n1 1\n255\n\x00"
        w = width or max(len(r.db) for r in self.rows)
        pixels = bytearray()
        for r in self.rows:
            cols = self._resample_row(r, w)
            if len(cols) < w:                      # pad short rows
                cols = cols + [0.0] * (w - len(cols))
            for i in cols:
                pixels.append(int(i * 255 + 0.5))
        h = len(self.rows)
        header = f"P5\n{w} {h}\n255\n".encode("ascii")
        return header + bytes(pixels)
