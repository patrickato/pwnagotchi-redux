"""Glass-box firehose — one verbose, time-ordered stream of what the box did.

The platform already records its decisions (the signed auth log) and what it saw
(the sighting store), each with a reason. This merges them into a single
chronological stream so you can watch the whole machine think in one place — the
learning view: every scope change, every campaign/pivot decision, every device
observed, interleaved by time.

Read-only. It's a LAB diagnostic (far too noisy and revealing for a field boot),
so the CLI gates it to LAB; here the merge logic is pure and testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class FirehoseLine:
    ts: float
    source: str                 # authlog | wifi | ble | ...
    text: str


def _detail(d) -> str:
    if not isinstance(d, dict):
        return ""
    return " ".join(f"{k}={v}" for k, v in sorted(d.items()))


def firehose(authlog_entries: Sequence = (), sightings: Sequence = (), *,
             limit: int = 200) -> List[FirehoseLine]:
    """Merge auth-log decisions and sightings into one time-ordered stream
    (most-recent `limit` lines). Pure: pass records in, get lines out."""
    lines: List[FirehoseLine] = []
    for e in authlog_entries:
        ts = float(getattr(e, "ts", 0.0) or 0.0)
        lines.append(FirehoseLine(ts=ts, source="authlog",
                                  text=f"#{getattr(e, 'seq', '?')} {getattr(e, 'event', '')} "
                                       f"{_detail(getattr(e, 'detail', {}))}".rstrip()))
    for s in sightings:
        ts = float(getattr(s, "ts", 0.0) or 0.0)
        bits = [getattr(s, "mac", "") or "?"]
        if getattr(s, "ssid", ""):
            bits.append(repr(s.ssid))
        if getattr(s, "rssi", None) is not None:
            bits.append(f"{s.rssi}dBm")
        if getattr(s, "channel", None) is not None:
            bits.append(f"ch{s.channel}")
        lines.append(FirehoseLine(ts=ts, source=(getattr(s, "kind", "") or "sighting"),
                                  text=" ".join(bits)))
    lines.sort(key=lambda l: l.ts)
    return lines[-limit:] if limit and limit > 0 else lines
