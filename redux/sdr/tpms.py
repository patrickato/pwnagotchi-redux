"""TPMS re-identification — spot returning vehicles by their tire sensors.

Every car continuously broadcasts its four tire-pressure sensor IDs (315 MHz in
North America, 433.92 MHz elsewhere), decodable by rtl_433. Those IDs don't
change, so logging them turns the driveway/road into a re-visit detector: "this
sensor set came back" means that vehicle came back. Pure recon — receive-only,
no transmit — and a neat extension of the device's existing re-identification
machinery.

Works on the rtl_433 JSON records the SDR layer already ingests. Honest: it
reports what was actually heard (sensor id, count, span, any pressure/temp the
decode carried); "returning" means the SAME id was seen across a real time gap,
not a single burst. Pure/testable — operates on a list of records, no radio.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

# rtl_433 marks TPMS either with type:"TPMS" or a model name containing TPMS.
def is_tpms(record: dict) -> bool:
    if not isinstance(record, dict):
        return False
    if str(record.get("type", "")).upper() == "TPMS":
        return True
    return "tpms" in str(record.get("model", "")).lower()


@dataclass(frozen=True)
class TpmsReading:
    sensor_id: str
    model: str
    ts: float
    pressure_kpa: Optional[float] = None
    temperature_c: Optional[float] = None


def _epoch(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        import calendar
        import time
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return float(calendar.timegm(time.strptime(value[:19], fmt)))
            except ValueError:
                continue
    return 0.0


def _pressure_kpa(rec: dict) -> Optional[float]:
    for k, scale in (("pressure_kPa", 1.0), ("pressure_PSI", 6.89476),
                     ("pressure_bar", 100.0), ("pressure_kpa", 1.0)):
        if rec.get(k) is not None:
            try:
                return round(float(rec[k]) * scale, 1)
            except (TypeError, ValueError):
                return None
    return None


def tpms_from_record(rec: dict) -> Optional[TpmsReading]:
    """One rtl_433 JSON record → a TpmsReading, or None if it isn't TPMS / has no id."""
    if not is_tpms(rec):
        return None
    sid = rec.get("id")
    if sid is None:
        return None
    sid = str(sid)
    model = str(rec.get("model") or "TPMS")
    temp = rec.get("temperature_C")
    try:
        temp = float(temp) if temp is not None else None
    except (TypeError, ValueError):
        temp = None
    return TpmsReading(sensor_id=sid, model=model, ts=_epoch(rec.get("time")),
                       pressure_kpa=_pressure_kpa(rec), temperature_c=temp)


@dataclass(frozen=True)
class TpmsVehicle:
    sensor_id: str
    model: str
    count: int
    first_seen: float
    last_seen: float
    span_s: float
    returning: bool
    last_pressure_kpa: Optional[float] = None
    reason: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def reidentify(records: Iterable[dict], *, min_gap_s: float = 300.0) -> List[TpmsVehicle]:
    """Group TPMS readings by sensor id and flag which have RETURNED — seen again
    after a gap of at least `min_gap_s` (so a single multi-packet burst isn't a
    'return'). Returning vehicles first, then by how many times seen."""
    by_id: Dict[str, List[TpmsReading]] = {}
    for rec in records:
        r = tpms_from_record(rec)
        if r is not None:
            by_id.setdefault(r.sensor_id, []).append(r)

    out: List[TpmsVehicle] = []
    for sid, reads in by_id.items():
        reads.sort(key=lambda r: r.ts)
        ts = [r.ts for r in reads]
        span = (ts[-1] - ts[0]) if ts else 0.0
        # returning = at least two readings separated by a real gap
        returning = any((ts[i] - ts[i - 1]) >= min_gap_s for i in range(1, len(ts)))
        last_p = next((r.pressure_kpa for r in reversed(reads) if r.pressure_kpa is not None), None)
        if returning:
            reason = (f"RETURNED: sensor {sid} seen {len(reads)}x over "
                      f"{span/3600:.1f}h — a vehicle that came back")
        else:
            reason = f"seen {len(reads)}x in one pass ({span:.0f}s span)"
        out.append(TpmsVehicle(sensor_id=sid, model=reads[0].model, count=len(reads),
                               first_seen=ts[0], last_seen=ts[-1], span_s=span,
                               returning=returning, last_pressure_kpa=last_p, reason=reason))
    out.sort(key=lambda v: (v.returning, v.count, v.span_s), reverse=True)
    return out
