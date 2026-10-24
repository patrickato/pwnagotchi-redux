"""rtl_power spectrum-sweep analysis — "listen to the bands".

`rtl_power` sweeps a frequency range and writes CSV rows, each covering a band
of bins:

    date, time, Hz_low, Hz_high, Hz_step, n_samples, dB, dB, dB, ...

This turns that firehose into something legible: a noise-floor estimate and the
**active peaks** — the frequencies where something is actually transmitting,
ranked by how far above the floor they sit. That's the scanner/"what's on the
air right now" view, without inventing anything: every number comes straight
from the measured dB bins.

Pure and testable: it parses text/rows, no radio. The caller runs rtl_power (on
hardware) or feeds a recorded CSV (in tests).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class Bin:
    freq_hz: float
    db: float


@dataclass(frozen=True)
class Peak:
    freq_hz: float
    db: float
    above_floor_db: float

    @property
    def freq_mhz(self) -> float:
        return self.freq_hz / 1e6

    def describe(self) -> str:
        return f"{self.freq_mhz:.4f} MHz @ {self.db:.1f} dB ({self.above_floor_db:+.1f} above floor)"


@dataclass(frozen=True)
class SpectrumSweep:
    bins: Tuple[Bin, ...]
    noise_floor_db: float
    peaks: Tuple[Peak, ...]

    def to_dict(self) -> dict:
        return {
            "bins": len(self.bins),
            "noise_floor_db": round(self.noise_floor_db, 2),
            "span_mhz": [round(self.bins[0].freq_hz / 1e6, 4),
                         round(self.bins[-1].freq_hz / 1e6, 4)] if self.bins else [],
            "peaks": [{"freq_mhz": round(p.freq_mhz, 4), "db": round(p.db, 2),
                       "above_floor_db": round(p.above_floor_db, 2)} for p in self.peaks],
        }


def _parse_row(line: str) -> List[Bin]:
    """One rtl_power CSV row -> the Bins it covers. Malformed rows -> []."""
    parts = [p.strip() for p in (line or "").split(",")]
    if len(parts) < 7:
        return []
    try:
        hz_low = float(parts[2]); hz_high = float(parts[3]); hz_step = float(parts[4])
    except ValueError:
        return []
    db_vals = []
    for p in parts[6:]:
        try:
            v = float(p)
        except ValueError:
            continue
        if v == v and abs(v) != float("inf"):   # drop NaN/inf
            db_vals.append(v)
    if not db_vals or hz_step <= 0:
        return []
    out = []
    for i, db in enumerate(db_vals):
        f = hz_low + i * hz_step
        if f > hz_high + hz_step:                # guard against ragged rows
            break
        out.append(Bin(freq_hz=f, db=db))
    return out


def parse_rtl_power(lines: Iterable[str], *, peak_threshold_db: float = 8.0,
                    max_peaks: int = 20) -> SpectrumSweep:
    """Parse rtl_power CSV into a sweep with a noise floor and ranked peaks.

    noise floor = median of all bins (robust to a few strong signals). A bin is a
    peak when it sits `peak_threshold_db` above the floor; peaks are merged if
    adjacent (same signal spread over bins) and returned strongest-first.
    """
    bins: List[Bin] = []
    for line in lines:
        bins.extend(_parse_row(line))
    if not bins:
        return SpectrumSweep(bins=(), noise_floor_db=float("nan"), peaks=())
    floor = statistics.median(b.db for b in bins)
    # candidate peaks above the floor
    raw = [(i, b) for i, b in enumerate(bins) if (b.db - floor) >= peak_threshold_db]
    # merge adjacent bins into one peak (keep the strongest bin of each run)
    peaks: List[Peak] = []
    run_best: Optional[Tuple[int, Bin]] = None
    last_idx = None
    for i, b in raw:
        if last_idx is not None and i == last_idx + 1:
            if run_best is None or b.db > run_best[1].db:
                run_best = (i, b)
        else:
            if run_best is not None:
                rb = run_best[1]
                peaks.append(Peak(rb.freq_hz, rb.db, rb.db - floor))
            run_best = (i, b)
        last_idx = i
    if run_best is not None:
        rb = run_best[1]
        peaks.append(Peak(rb.freq_hz, rb.db, rb.db - floor))
    peaks.sort(key=lambda p: p.db, reverse=True)
    return SpectrumSweep(bins=tuple(bins), noise_floor_db=floor,
                         peaks=tuple(peaks[:max_peaks]))
