"""Passive PMKID sweep — zero-transmit loot.

Many WPA2-PSK APs volunteer the PMKID in the first EAPOL message (M1) whenever
*any* client associates. So you don't have to deauth anyone, or even send an
association request — sit in monitor mode and listen, and the APs hand you a
crackable hash for free. This is the quietest loot path there is: nothing leaves
your antenna, so there is nothing for a WIDS/blue team to detect.

This is the harvest-and-correlate layer over that passive stream — the place
where "we have A" + "we have B" becomes "so we get C":

  - a PMKID sighting gives the BSSID; the beacon/probe inventory gives the SSID,
    channel and security  → an enriched, crackable loot record
  - crossed with the crackability classifier (#4 pre-flight) → is the hash worth
    cracking, and by what method?
  - crossed with the central Scope → is it an authorized target? (LAB: unscoped
    by default — everything in earshot is fair game)
  - crossed with the already-captured / retired set → is this NEW loot, or an AP
    the box already owns?
  - the SSID seeds the wordlist forge (#3); the outcome feeds the experience
    store so adaptive capture (#4) learns that "pmkid" paid off here.

Zero-transmit by construction: the sweep only ever READS frames. Pure and
testable — feed it `Frame` objects, inject the scope + the retired set, no radio.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from ..detect.frames import Frame, FrameType

# live-state file the UIs read (same pattern as stealth.json) — the last sweep's
# summary, so the TFT/web/phone can show "N crackable PMKIDs harvested silently"
# without re-reading capture files on every status() call.
DEFAULT_PMKID_STATE = "/captures/live/pmkid.json"


def _norm(bssid: str) -> str:
    return (bssid or "").strip().lower()


def _colonize(hex12: str) -> str:
    """'aabbccddeeff' -> 'aa:bb:cc:dd:ee:ff' (already-colon input passes through)."""
    v = (hex12 or "").strip().lower().replace(":", "")
    if len(v) != 12 or any(c not in "0123456789abcdef" for c in v):
        return _norm(hex12)
    return ":".join(v[i:i + 2] for i in range(0, 12, 2))


def parse_22000_pmkid(text: str) -> List[Frame]:
    """Turn the real hashcat-22000 file the capture engine writes into PMKID
    `Frame`s the sweep can correlate — so A (the .22000 the box already produces)
    + B (scope/forge/crackability) becomes C (a ranked passive-loot report).

    A PMKID line is `WPA*01*<pmkid>*<mac_ap>*<mac_sta>*<essid_hex>*...`; only type
    01 (PMKID) is harvested here — type 02 is an EAPOL handshake, a different path.
    Malformed lines are skipped, never fabricated."""
    out: List[Frame] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "*" not in line:
            continue
        p = line.split("*")
        if len(p) < 6 or p[0].upper() != "WPA" or p[1] != "01":
            continue
        bssid = _colonize(p[3])
        try:
            ssid = bytes.fromhex(p[5]).decode("utf-8", "replace") if p[5] else ""
        except ValueError:
            ssid = ""
        out.append(Frame(type=FrameType.PMKID, ts=0.0, bssid=bssid, ssid=ssid,
                         security="wpa2-psk"))
    return out


@dataclass(frozen=True)
class PmkidHit:
    """One AP that leaked a crackable PMKID without us transmitting a thing."""
    bssid: str
    ssid: str = ""
    channel: Optional[int] = None
    security: str = ""
    rssi: Optional[int] = None
    first_seen: float = 0.0
    last_seen: float = 0.0
    count: int = 1
    crackable: bool = True
    method: str = ""
    reason: str = ""
    in_scope: bool = True
    scope_reason: str = ""
    already_captured: bool = False

    @property
    def new_loot(self) -> bool:
        """Worth grabbing: crackable, authorized, and not already owned."""
        return self.crackable and self.in_scope and not self.already_captured

    def to_dict(self) -> dict:
        return {"bssid": self.bssid, "ssid": self.ssid, "channel": self.channel,
                "security": self.security, "rssi": self.rssi, "count": self.count,
                "first_seen": self.first_seen, "last_seen": self.last_seen,
                "crackable": self.crackable, "method": self.method, "reason": self.reason,
                "in_scope": self.in_scope, "scope_reason": self.scope_reason,
                "already_captured": self.already_captured, "new_loot": self.new_loot}


@dataclass
class PassivePmkidSweep:
    """Harvest PMKID sightings and correlate them into ranked, crackable loot.

    `scope`   — the central Scope (optional). None ⇒ LAB-unscoped: everything in
                earshot counts as authorized (the un-gated default the operator
                asked for). A Scope ⇒ each BSSID is checked with `authorize`.
    `retired` — BSSIDs the box already captured/ignores (crack/captured list), so
                the sweep flags already-owned APs instead of re-looting them.
    `inventory` — optional {bssid: {ssid, channel, security, rssi}} from the beacon
                tap, used to enrich a PMKID frame that didn't carry the SSID.
    """
    scope: object = None
    retired: frozenset = field(default_factory=frozenset)
    inventory: Dict[str, dict] = field(default_factory=dict)
    _hits: Dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.retired = frozenset(_norm(b) for b in (self.retired or ()))
        self.inventory = {_norm(b): v for b, v in (self.inventory or {}).items()}

    # ---- enrichment / correlation ------------------------------------------
    def learn_inventory(self, access_points: Sequence) -> None:
        """Fold in the passive AP inventory (CaptureTap.access_points()) so a bare
        PMKID frame inherits the SSID/channel the beacons already told us."""
        for ap in access_points or ():
            b = _norm(getattr(ap, "bssid", "") or (ap.get("bssid") if isinstance(ap, dict) else ""))
            if not b:
                continue
            get = (lambda k: getattr(ap, k, None)) if not isinstance(ap, dict) else ap.get
            self.inventory.setdefault(b, {})
            for k in ("ssid", "channel", "security", "rssi"):
                v = get(k)
                if v not in (None, ""):
                    self.inventory[b][k] = v

    def _assess(self, security: str, ssid: str, pmf: str) -> tuple:
        """Crackability of a PMKID loot record. A PMKID sighting is itself strong
        evidence of a PSK path, so default to crackable; let the classifier add the
        concrete method, and only stand down for a clearly non-PSK security."""
        try:
            from ..classify.crackability import classify
            a = classify({"security": security, "ssid": ssid, "pmf": pmf})
            if a.offline_crackable:
                return True, a.method, a.reason
            # classifier says no, but we literally saw a PMKID — trust the evidence,
            # flag the disagreement honestly instead of discarding free loot.
            if security and any(t in security.lower() for t in ("sae", "wpa3", "owe", "enterprise", "802.1x", "eap", "open")):
                return False, "none", f"PMKID seen but security is {security} — {a.reason}"
        except Exception:
            pass
        return True, "capture PMKID → hashcat -m 22000", ("PMKID present ⇒ WPA2-PSK path exists"
                                                          + (f" (SSID '{ssid}' is the salt)" if ssid else ""))

    # ---- ingest -------------------------------------------------------------
    def feed(self, frame: Frame) -> Optional[PmkidHit]:
        """Consume one frame; act only on PMKID sightings. Returns the (updated)
        hit for a PMKID frame, else None. Idempotent per BSSID — repeat sightings
        bump the count and last-seen, they don't duplicate."""
        if frame is None or frame.type is not FrameType.PMKID:
            return None
        b = _norm(frame.bssid)
        if not b:
            return None
        inv = self.inventory.get(b, {})
        ssid = frame.ssid or inv.get("ssid", "")
        channel = frame.channel if frame.channel is not None else inv.get("channel")
        security = frame.security or inv.get("security", "")
        rssi = frame.rssi if frame.rssi is not None else inv.get("rssi")
        rec = self._hits.get(b)
        if rec is None:
            rec = {"bssid": b, "ssid": ssid, "channel": channel, "security": security,
                   "rssi": rssi, "first_seen": frame.ts, "last_seen": frame.ts, "count": 0}
            self._hits[b] = rec
        rec["count"] += 1
        rec["last_seen"] = frame.ts
        # late-arriving enrichment wins over blanks
        rec["ssid"] = rec["ssid"] or ssid
        rec["channel"] = rec["channel"] if rec["channel"] is not None else channel
        rec["security"] = rec["security"] or security
        if rssi is not None:
            rec["rssi"] = rssi
        return self._resolve(rec)

    def feed_many(self, frames: Sequence[Frame]) -> List[PmkidHit]:
        out = []
        for f in frames or ():
            h = self.feed(f)
            if h is not None:
                out.append(h)
        return out

    # ---- resolution ---------------------------------------------------------
    def _resolve(self, rec: dict) -> PmkidHit:
        crackable, method, reason = self._assess(rec["security"], rec["ssid"], "")
        in_scope, scope_reason = True, "LAB: unscoped — all APs in earshot are fair game"
        if self.scope is not None:
            try:
                in_scope, scope_reason = self.scope.authorize(bssid=rec["bssid"], ssid=rec["ssid"] or None)
            except Exception as e:
                in_scope, scope_reason = False, f"scope check failed: {type(e).__name__}"
        return PmkidHit(
            bssid=rec["bssid"], ssid=rec["ssid"], channel=rec["channel"],
            security=rec["security"], rssi=rec["rssi"],
            first_seen=rec["first_seen"], last_seen=rec["last_seen"], count=rec["count"],
            crackable=crackable, method=method, reason=reason,
            in_scope=in_scope, scope_reason=scope_reason,
            already_captured=rec["bssid"] in self.retired)

    # ---- views --------------------------------------------------------------
    def hits(self) -> List[PmkidHit]:
        """All harvested PMKIDs, ranked: new crackable loot first, then by signal."""
        out = [self._resolve(r) for r in self._hits.values()]
        out.sort(key=lambda h: (not h.new_loot, not h.in_scope, not h.crackable,
                                -(h.rssi if h.rssi is not None else -999), h.bssid))
        return out

    def new_loot(self) -> List[PmkidHit]:
        """Only the APs worth cracking — the actionable output of the sweep."""
        return [h for h in self.hits() if h.new_loot]

    def summary(self) -> dict:
        hits = self.hits()
        new = [h for h in hits if h.new_loot]
        return {
            "transmit": "zero — nothing was sent; PMKIDs harvested passively",
            "aps_leaking_pmkid": len(hits),
            "crackable": sum(1 for h in hits if h.crackable),
            "in_scope": sum(1 for h in hits if h.in_scope),
            "already_captured": sum(1 for h in hits if h.already_captured),
            "new_loot": len(new),
            "footprint": "silent",
            "note": (f"{len(new)} crackable PMKID(s) harvested without transmitting a frame"
                     if new else "no new crackable PMKIDs yet — keep listening"),
        }

    def wordlist_seeds(self, hit: PmkidHit, *, tokens: Sequence[str] = ()) -> List[str]:
        """Hand a hit straight to the wordlist forge: the SSID (and vendor, via the
        BSSID OUI) seed a targeted candidate list — A (we have the SSID) + B (we
        have the forge) → C (a tuned wordlist, ready before the crack even starts)."""
        try:
            from .forge import forge
            return list(forge(hit.ssid or "", tokens=tokens, bssid=hit.bssid or None))
        except Exception:
            return []

    def save_state(self, path: str = DEFAULT_PMKID_STATE) -> bool:
        """Write the sweep summary + top hits to the live-state file the UIs read.
        Fail-safe: a write that can't happen just returns False, never raises."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = {"ts": time.time(), "summary": self.summary(),
                       "hits": [h.to_dict() for h in self.hits()[:50]]}
            tmp = f"{path}.tmp"
            with open(tmp, "w") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
            return True
        except Exception:
            return False


def load_state(path: str = DEFAULT_PMKID_STATE) -> dict:
    """Read the last sweep's summary for the status model. Fail-safe → {}."""
    try:
        with open(path) as fh:
            return json.load(fh) or {}
    except Exception:
        return {}
