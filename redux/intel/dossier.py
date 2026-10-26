"""Target dossier — everything the box knows about one AP, in one view.

Recon scatters what's known about a target across the sightings store, the
crackability classifier, the PMKID sweep, the captured list and the OUI table.
The dossier gathers it into a single glass-box card: who it is, how it's secured
(and whether a "WPA3" sticker hides a crackable path), how worth attacking it is,
what's already been seen, where/when, and the one recommended next move.

A + B + ... ⇒ C: every source about a target ⇒ one answer. Pure/deterministic
over injected facts; composes crackability, downgrade (#transition), the
target-value radar and OUI vendor lookup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set


def _norm(b: str) -> str:
    return (b or "").strip().lower()


@dataclass(frozen=True)
class Dossier:
    bssid: str
    ssid: str
    vendor: str
    channel: Optional[int]
    band: str
    security_mode: str
    downgradable: bool
    crackable: bool
    method: str
    value_score: int
    pmkid_seen: bool
    clients: int
    wps: bool
    already_owned: bool
    first_seen: Optional[float]
    last_seen: Optional[float]
    located: Optional[dict]
    recommended: str
    facts: tuple

    def to_dict(self) -> dict:
        return {"bssid": self.bssid, "ssid": self.ssid, "vendor": self.vendor,
                "channel": self.channel, "band": self.band,
                "security_mode": self.security_mode, "downgradable": self.downgradable,
                "crackable": self.crackable, "method": self.method,
                "value_score": self.value_score, "pmkid_seen": self.pmkid_seen,
                "clients": self.clients, "wps": self.wps, "already_owned": self.already_owned,
                "first_seen": self.first_seen, "last_seen": self.last_seen,
                "located": self.located, "recommended": self.recommended,
                "facts": list(self.facts)}


def _recommend(*, owned, crackable, pmkid_seen, wps, clients, downgradable, mode) -> str:
    if owned:
        return "already owned — skip (on the captured list)"
    if not crackable:
        return f"no offline path ({mode}) — move on"
    if pmkid_seen:
        return "crack the PMKID already harvested (zero further transmit)"
    if downgradable and mode == "wpa3-transition":
        return "force a client to the WPA2 path, then capture PMKID/handshake"
    if wps:
        return "try WPS pixie-dust"
    if clients > 0:
        return "deauth a client to force a handshake, then crack"
    return "capture a PMKID/handshake, then crack with an environment wordlist"


def build_dossier(bssid: str, *, ssid: str = "", security: str = "wpa2-psk",
                  channel: Optional[int] = None, rssi: Optional[int] = None,
                  clients: int = 0, pmkid_seen: bool = False, wps: bool = False,
                  retired: Optional[Set[str]] = None,
                  first_seen: Optional[float] = None, last_seen: Optional[float] = None,
                  lat: Optional[float] = None, lon: Optional[float] = None,
                  akm=None) -> Dossier:
    """Assemble the one-view dossier for a target from whatever is known."""
    from ..classify.downgrade import assess_downgrade
    from ..intel.target_radar import TargetObservation, score_target
    from ..radio.airwave_dvr import band_of_channel
    retired = {_norm(b) for b in (retired or set())}
    b = _norm(bssid)

    net = {"encryption": security, "security": security, "ssid": ssid}
    if akm:
        net["akm"] = akm
    dg = assess_downgrade(net)
    # a downgradable AP is crackable via its WPA2 path — score that path, not the
    # strong label the sticker advertises.
    radar_security = "wpa2-psk" if dg.downgradable else security
    rt = score_target(TargetObservation(b, ssid, radar_security, rssi, channel, clients,
                                        pmkid_seen, wps), retired=retired)
    crackable = rt.crackable or dg.downgradable
    try:
        from ..geo.oui import lookup_oui
        vendor = lookup_oui(b) or "unknown"
    except Exception:
        vendor = "unknown"

    facts: List[str] = []
    facts.append(f"security: {dg.mode}" + (" — DOWNGRADABLE" if dg.downgradable else ""))
    facts.append(f"crackable: {'yes — ' + rt.method if crackable else 'no'}")
    if pmkid_seen:
        facts.append("PMKID already harvested (free crackable hash)")
    if clients:
        facts.append(f"{clients} client(s) associated")
    if wps:
        facts.append("WPS enabled")
    if vendor and vendor != "unknown":
        facts.append(f"vendor: {vendor}")
    if rssi is not None:
        facts.append(f"signal: {rssi} dBm ({band_of_channel(channel)} GHz)")
    if b in retired:
        facts.append("on the captured/ignore list — already owned")

    located = {"lat": lat, "lon": lon} if (lat is not None and lon is not None) else None
    rec = _recommend(owned=b in retired, crackable=crackable, pmkid_seen=pmkid_seen,
                     wps=wps, clients=clients, downgradable=dg.downgradable, mode=dg.mode)
    return Dossier(bssid=b, ssid=ssid, vendor=vendor, channel=channel,
                   band=band_of_channel(channel), security_mode=dg.mode,
                   downgradable=dg.downgradable, crackable=crackable, method=rt.method,
                   value_score=rt.score, pmkid_seen=pmkid_seen, clients=clients, wps=wps,
                   already_owned=b in retired, first_seen=first_seen, last_seen=last_seen,
                   located=located, recommended=rec, facts=tuple(facts))
