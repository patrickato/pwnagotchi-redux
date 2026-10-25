"""Crack ROI triage + passive-capture forecast — spend effort where it pays.

Two questions this answers, both from data the box already has, and both as a
**learning aid** — every number is a rough, clearly-labeled heuristic for
*ordering* work, never a promise of success:

1. **Crack ROI** — of the handshakes/PMKIDs already captured (the `.hc22000`
   queue), which are worth the GPU/CPU time first? A mode-22000 artifact is
   WPA/WPA2-PSK by construction, so all are offline-crackable; what differs is
   how likely a given wordlist is to land. The SSID *name* is the cheap signal:
   vendor/default-style names ("NETGEAR58", "SpectrumSetup-A1") often pair with
   factory or common-wordlist keys; all-numeric names often hide a phone number;
   a custom name depends entirely on the owner. We rank by that prior and point
   at the wordlist most likely to work.

2. **Passive-capture forecast** — for networks devices are *probing* for (the
   harvested PNL client counts, real data), how long until you'd likely catch a
   handshake just by waiting, and when is a clientless PMKID or an in-scope
   deauth the better move? More clients looking → more reassociations → shorter
   expected wait. Zero clients → waiting is pointless; go PMKID.

Pure/deterministic. The models are intentionally simple and transparent so the
operator can see *why* a target ranks where it does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .ingest import valid_22000_line

# Representative vendor/default SSID markers. Not exhaustive and not a claim that
# any given network is weak — just names that often ship factory/common keys, so
# a targeted wordlist is worth trying before a generic one.
_VENDOR_MARKERS = (
    "netgear", "linksys", "dlink", "d-link", "tp-link", "tplink", "asus",
    "belkin", "orbi", "arris", "arris-", "spectrumsetup", "myspectrumwifi",
    "frontier", "fios", "verizon", "att-wifi", "setup", "home-", "tendawifi",
    "ziggo", "vodafone", "telekom", "sky", "bt-", "virginmedia",
)
_GUEST_MARKERS = ("guest", "hotspot", "public", "free wifi", "freewifi")


def ssid_crack_prior(ssid: str) -> Tuple[float, str, str]:
    """A rough prior that a wordlist attack lands, a wordlist hint, and the
    glass-box rationale. Heuristic — meant for *ordering* a crack queue, not as
    a probability of success. Returns (prob 0..1, wordlist_hint, rationale)."""
    name = (ssid or "").strip()
    low = name.lower()
    if not name:
        return 0.15, "rockyou + best64 rules", "hidden/empty SSID — no name signal; generic wordlist"
    if any(m in low for m in _VENDOR_MARKERS):
        return 0.40, "vendor/default list, then rockyou+rules", \
            "vendor/default-style name — factory or common-wordlist keys are worth trying first"
    compact = name.replace(" ", "").replace("-", "")
    if compact.isdigit() and len(compact) >= 6:
        return 0.30, "digit masks (?d?d?d…) + phone-number patterns", \
            "all-numeric name — owners often reuse a numeric/phone key; try digit masks"
    if any(m in low for m in _GUEST_MARKERS):
        return 0.25, "common shared/guest keys + rockyou", \
            "guest/shared-style name — often a simple shared key"
    return 0.15, "rockyou + best64 rules", \
        "custom name — success depends on the owner's password; generic wordlist + rules"


# --- crack queue ------------------------------------------------------------ #

@dataclass(frozen=True)
class CrackTarget:
    ssid: str
    bssid: str
    artifacts: Tuple[str, ...]      # {"pmkid","handshake"} we hold for this AP
    prob: float
    wordlist_hint: str
    rationale: str
    priority: float                 # 0..100, higher = crack sooner

    @property
    def tier(self) -> str:
        return "high" if self.priority >= 35 else "medium" if self.priority >= 22 else "low"


def _bssid_colon(twelve_hex: str) -> str:
    v = twelve_hex.lower()
    return ":".join(v[i:i + 2] for i in range(0, 12, 2)) if len(v) == 12 else v


def parse_22000_targets(text: str) -> List[Tuple[str, str, str]]:
    """Extract (ssid, bssid, artifact) from hc22000 text. Uses ingest's own
    structural validator so the format stays single-sourced; type 01 = pmkid,
    02 = handshake; field 5 is the hex ESSID (may be empty)."""
    out: List[Tuple[str, str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not valid_22000_line(line):
            continue
        f = line.split("*")
        artifact = "pmkid" if f[1] == "01" else "handshake"
        bssid = _bssid_colon(f[3])
        try:
            ssid = bytes.fromhex(f[5]).decode("utf-8", "replace") if f[5] else ""
        except ValueError:
            ssid = ""
        out.append((ssid, bssid, artifact))
    return out


def triage_crack_queue(targets: Sequence[Tuple[str, str, str]]) -> List[CrackTarget]:
    """Rank captured targets by crack ROI. `targets`: (ssid, bssid, artifact)
    tuples (e.g. from parse_22000_targets). One row per BSSID; artifact types
    merged. Sorted highest-ROI first."""
    merged: Dict[str, Dict] = {}
    for ssid, bssid, artifact in targets:
        rec = merged.setdefault(bssid, {"ssid": ssid, "artifacts": set()})
        rec["artifacts"].add(artifact)
        if ssid and not rec["ssid"]:
            rec["ssid"] = ssid
    results: List[CrackTarget] = []
    for bssid, rec in merged.items():
        prob, hint, why = ssid_crack_prior(rec["ssid"])
        # readiness: a PMKID is a single clean frame (no client needed to re-check),
        # a 4-way is equally crackable; holding both is a small confidence bump.
        both = len(rec["artifacts"]) > 1
        priority = round(prob * 100 * (1.05 if both else 1.0), 1)
        results.append(CrackTarget(
            ssid=rec["ssid"], bssid=bssid,
            artifacts=tuple(sorted(rec["artifacts"])),
            prob=prob, wordlist_hint=hint, rationale=why, priority=priority))
    results.sort(key=lambda t: (-t.priority, t.ssid or "~", t.bssid))
    return results


# --- passive-capture forecast ----------------------------------------------- #

@dataclass(frozen=True)
class CaptureForecast:
    ssid: str
    clients: int
    mean_hours: Optional[float]     # expected passive wait; None when not applicable
    eta: str                        # human phrasing
    recommend: str                  # the suggested move
    reasons: Tuple[str, ...] = ()


def _eta_phrase(hours: float) -> str:
    if hours < 0.5:
        return "minutes (clients are active)"
    if hours < 2:
        return f"~{hours:.1f}h"
    if hours < 12:
        return f"~{hours:.0f}h (slow)"
    return "many hours — don't wait"


def capture_forecast(ssid: str, clients: int, *,
                     reassoc_per_hour: float = 0.5) -> CaptureForecast:
    """Rough expected wait for a *passive* handshake given how many distinct
    clients are probing for this SSID, and what to do instead when waiting is a
    poor bet. `reassoc_per_hour` is the assumed per-client reassociation rate
    (~every 2h by default) — a transparent knob, not a measurement."""
    clients = max(0, int(clients))
    if clients <= 0:
        return CaptureForecast(
            ssid=ssid, clients=0, mean_hours=None, eta="no clients probing",
            recommend="PMKID (clientless) — a passive 4-way is unlikely with no clients present",
            reasons=("no devices seen looking for this SSID — nothing to reassociate",))
    rate = clients * max(1e-6, reassoc_per_hour)
    mean_hours = 1.0 / rate
    if mean_hours >= 2:
        rec = ("deauth one client to force a reassociation now (in-scope only) — "
               "or run a clientless PMKID attempt")
    else:
        rec = "wait (passive) — a reassociation is likely soon; deauth only if you need it now"
    return CaptureForecast(
        ssid=ssid, clients=clients, mean_hours=round(mean_hours, 2),
        eta=_eta_phrase(mean_hours), recommend=rec,
        reasons=(f"{clients} client(s) probing × ~{reassoc_per_hour}/h reassoc "
                 f"≈ {rate:.1f} capture chances/h",))


def triage_capture(ssid_rows: Sequence[dict], *,
                   reassoc_per_hour: float = 0.5) -> List[CaptureForecast]:
    """Forecast per SSID from ProbeHarvester.ssids() rows ({ssid, client_count}).
    Most-capturable (most clients) first."""
    out = [capture_forecast(r.get("ssid", ""), int(r.get("client_count", 0) or 0),
                            reassoc_per_hour=reassoc_per_hour)
           for r in ssid_rows if r.get("ssid")]
    out.sort(key=lambda c: (-c.clients, c.ssid))
    return out
