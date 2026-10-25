"""Threat radar — counter-surveillance from what the box passively sees.

Flip the toolkit to blue team: instead of attacking, watch for someone attacking
*you*. This analyses the sightings the box already records (and the harvested
PNL) for the signatures of a wireless attack aimed at you:

  * evil_twin     — one SSID advertised from two or more BSSIDs (the classic
                    clone). HIGH when one of yours is being impersonated.
  * pnl_lure      — an AP advertising a network your own devices probe for,
                    from a BSSID you don't recognise (a KARMA-style lure your
                    phone might auto-join).
  * new_strong_ap — a brand-new AP that appeared recently with a close/strong
                    signal (worth a look if you've been stationary).

Real-data-only and glass-box: every alert lists the exact BSSIDs and says what
to do (from the purple-team knowledge base). It is **honest about its blind
spot** — deauth floods and RF jamming are frame-level events that a stored
sighting can't show; detecting those needs the live monitor, and the report
says so rather than pretending.

Pure/deterministic: sightings in, alerts out.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .defend import explain
from ..geo.mac_normalize import normalize_mac


@dataclass(frozen=True)
class Alert:
    kind: str                  # evil_twin | pnl_lure | new_strong_ap
    severity: str              # high | medium | low
    ssid: str
    bssids: Tuple[str, ...]
    detail: str
    advice: str


@dataclass(frozen=True)
class RadarReport:
    alerts: Tuple[Alert, ...]
    scanned_aps: int
    notes: Tuple[str, ...]     # honest scope limits (e.g. deauth/jam blind spot)


_SEV_RANK = {"high": 0, "medium": 1, "low": 2}


def _advice(technique_key: str, fallback: str) -> str:
    t = explain(technique_key)
    return t.mitigation[0] if (t and t.mitigation) else fallback


def scan_sightings(rows: Sequence, *, pnl_ssids: Sequence[str] = (),
                   trusted_bssids: Sequence[str] = (), now: Optional[float] = None,
                   new_window_s: float = 3600.0, strong_rssi: int = -50) -> RadarReport:
    """Analyse wifi sightings for attacks aimed at the operator."""
    now = time.time() if now is None else now
    pnl = {s for s in pnl_ssids if s}
    trusted = {normalize_mac(b) for b in trusted_bssids if b}

    # group wifi APs by SSID
    by_ssid: Dict[str, set] = {}
    wifi = 0
    for r in rows:
        if (getattr(r, "kind", "wifi") or "wifi") != "wifi":
            continue
        wifi += 1
        ssid = getattr(r, "ssid", "") or ""
        mac = normalize_mac(getattr(r, "mac", "") or "")
        if ssid and mac:
            by_ssid.setdefault(ssid, set()).add(mac)

    alerts: List[Alert] = []

    # 1) evil twin — one SSID from multiple BSSIDs
    for ssid, bssids in by_ssid.items():
        if len(bssids) < 2:
            continue
        has_trusted = bool(bssids & trusted)
        impostors = bssids - trusted
        if has_trusted and impostors:
            sev, why = "high", (f"SSID {ssid!r} is advertised by a trusted AP AND "
                                f"{len(impostors)} other BSSID(s) — your network is being cloned")
        elif ssid in pnl:
            sev, why = "high", (f"SSID {ssid!r} (a network your devices look for) is advertised "
                                f"by {len(bssids)} BSSIDs")
        else:
            sev, why = "medium", f"SSID {ssid!r} is advertised by {len(bssids)} different BSSIDs"
        alerts.append(Alert("evil_twin", sev, ssid, tuple(sorted(bssids)), why,
                            _advice("evil_twin", "verify the AP's BSSID; prefer 802.1X/cert validation")))

    # 2) PNL lure — an AP advertising a network your devices probe for, from an
    #    unrecognised BSSID (only single-BSSID ones here; multi-BSSID handled above)
    for ssid, bssids in by_ssid.items():
        if ssid not in pnl or len(bssids) >= 2:
            continue
        untrusted = bssids - trusted
        if not untrusted:
            continue
        alerts.append(Alert(
            "pnl_lure", "medium", ssid, tuple(sorted(untrusted)),
            f"an AP is advertising {ssid!r}, which your devices probe for — verify it's the real "
            "network, not a KARMA lure your device could auto-join",
            _advice("karma", "forget stale saved networks; disable auto-join to open networks")))

    # 3) new strong AP — recent first_seen + close signal
    for r in rows:
        if (getattr(r, "kind", "wifi") or "wifi") != "wifi":
            continue
        fs = getattr(r, "first_seen", None)
        rssi = getattr(r, "rssi", None)
        if fs is None or rssi is None:
            continue
        try:
            recent = (now - float(fs)) <= new_window_s
            close = int(rssi) >= strong_rssi
        except (TypeError, ValueError):
            continue
        if recent and close:
            ssid = getattr(r, "ssid", "") or "(hidden)"
            mac = normalize_mac(getattr(r, "mac", "") or "")
            if mac in trusted:
                continue
            alerts.append(Alert(
                "new_strong_ap", "low", ssid, (mac,),
                f"a strong new AP {ssid!r} ({mac}) at {int(rssi)} dBm appeared in the last "
                f"{int(new_window_s // 60)} min — worth a look if you've been stationary",
                "confirm you recognise this AP; an evil twin often shows up close and strong"))

    alerts.sort(key=lambda a: (_SEV_RANK.get(a.severity, 9), a.kind, a.ssid))
    notes = (
        "Blind spot: deauth floods and RF jamming are frame-level events a stored sighting "
        "can't show — detecting those live needs the monitor-mode interface (`redux explain deauth`).",
    )
    return RadarReport(alerts=tuple(alerts), scanned_aps=wifi, notes=notes)
