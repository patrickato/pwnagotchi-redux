"""Native TFT chart painter for actual observed Redux counter rates.

Does not generate samples or imply RF reception. No I/O besides drawing into
a supplied in-memory Pillow image.
"""
from __future__ import annotations

import math

_KEYS = ("events_per_min", "hs_events_per_min", "handoffs_per_min")
_MAX_RATE = 1e12


def _number(value):
    if type(value) not in (int, float):
        return None
    try:
        if math.isfinite(value) and 0 <= value <= _MAX_RATE:
            return float(value)
    except (OverflowError, ValueError, TypeError):
        pass
    return None


def points(view):
    """Strictly validate all chart data; never paint a partial false series."""
    if not isinstance(view, dict) or view.get("state") != "TRACKING":
        return []
    data = view.get("points")
    if not isinstance(data, list) or not 1 <= len(data) <= 150:
        return []
    out = []
    previous_age = float("inf")
    for row in data:
        if not isinstance(row, dict):
            return []
        age = _number(row.get("age_seconds"))
        vals = [_number(row.get(key)) for key in _KEYS]
        if (age is None or age > 300 or age >= previous_age
                or any(v is None for v in vals)):
            return []
        out.append((age, tuple(vals)))
        previous_age = age
    return out


def _compact(value):
    if value >= 1e9:
        return f"{value / 1e9:.1f}B"
    if value >= 1e6:
        return f"{value / 1e6:.1f}M"
    if value >= 1e3:
        return f"{value / 1e3:.0f}K"
    return f"{value:.1f}" if value < 10 else f"{value:.0f}"


def draw_trends(draw, view, *, font, ink, subtle, teal, amber, blue):
    """Paint three 5-minute fixed-time, 0-based rate traces on a 480x320 UI."""
    draw.text((20, 160), "MEASURED ENGINE COUNTER RATES / MIN",
              font=font(11, bold=True), fill=subtle)
    records = points(view)
    if not records:
        state = view.get("state", "NO SAMPLES") if isinstance(view, dict) else "NO SAMPLES"
        if state not in ("NO SAMPLES", "ONE SAMPLE", "TRACKING", "UNAVAILABLE",
                         "NOT CURRENT", "UNQUALIFIED", "COUNTERS UNKNOWN",
                         "ENGINE CHANGED", "CLOCK CONFLICT", "SAMPLE GAP",
                         "COUNTER RESET", "RATE INVALID", "STALE"):
            state = "UNVERIFIED"
        draw.text((20, 203), "NO VERIFIED RATE HISTORY",
                  font=font(16, bold=True), fill=amber)
        draw.text((20, 232), state, font=font(12), fill=subtle)
        draw.text((20, 271), "Engine events are not decoded RF proof.",
                  font=font(10), fill=subtle)
        return 0
    left, top, right, bottom = 25, 192, 453, 261
    maximum = max(v for _, values in records for v in values)
    scale = max(1.0, maximum)
    for part in (0, 1, 2):
        y = round(bottom - part * (bottom - top) / 2)
        draw.line((left, y, right, y), fill=(37, 58, 68), width=1)
    for key_index, color in enumerate((teal, amber, blue)):
        coords = []
        for age, vals in records:
            x = round(right - age / 300 * (right - left))
            y = round(bottom - vals[key_index] / scale * (bottom - top))
            coords.append((max(left, min(right, x)), max(top, min(bottom, y))))
        if len(coords) >= 2:
            draw.line(coords, fill=color, width=2)
        if coords:
            x, y = coords[-1]
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)
    draw.text((20, 177), "PEAK " + _compact(maximum) + " / MIN",
              font=font(10), fill=ink)
    draw.text((20, 265), "5m", font=font(9), fill=subtle)
    draw.text((433, 265), "NOW", font=font(9), fill=subtle)
    latest = records[-1][1]
    # Keep the latest measured rates visible even when a user cannot
    # distinguish individual points on the compact 480x320 chart.
    legend = ((f"EVENTS {_compact(latest[0])}", teal, 20),
              (f"HS EVENTS {_compact(latest[1])}", amber, 153),
              (f"HANDOFFS {_compact(latest[2])}", blue, 329))
    for name, color, x in legend:
        draw.rectangle((x, 286, x + 7, 291), fill=color)
        draw.text((x + 12, 281), name, font=font(9), fill=subtle)
    return len(records)
