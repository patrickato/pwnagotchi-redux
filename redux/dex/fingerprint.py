"""Device fingerprinting — identity that survives MAC randomization.

Modern phones rotate their MAC, so a MAC-keyed Dex loses a device the moment it
re-randomizes. But a device carries MAC-*independent* signatures: the set of
networks it probes for (its Preferred Network List) and the exact template of its
probe-request information elements. Two different MACs that share those are almost
certainly the same device. That turns the Dex from "MACs I've seen" into "devices
I've seen" — a logbook into a recon brain.

This module is the pure, testable identity math. The honesty line is strict,
because the failure mode here is a *false link* (claiming two devices are one):

  - **OUI is never a cross-MAC signal.** A randomized MAC's vendor prefix is
    meaningless, so it is used only when the MAC is *not* randomized — and even
    then it only labels, it never links two MACs together.
  - **Linking requires a MAC-independent signal** (PNL or IE template). With
    neither, a device is NOT linkable: each randomized MAC stays its own ephemeral
    identity, and we say so. "We couldn't link it" is never "it's new."
  - **Strength is reported, not hidden.** A link off a 1-network PNL is weak and
    labelled weak; a rich PNL or an IE template is strong. Every identity carries
    the evidence that formed it.

Rich PNL / IE capture needs the probe-request / raw-frame tap (the same gap the
raw-frame detectors have). The linker works on whatever features it's given, so
the store adapter here is best-effort today and gets sharper the moment that tap
exists — the logic doesn't change.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple


def is_randomized(mac: str) -> bool:
    """True if the MAC is locally-administered (randomized): bit 0x02 of the first
    octet set. Unparseable → treated as randomized (can't trust it as stable)."""
    mac = (mac or "").strip().lower()
    first = mac.split(":")[0] if ":" in mac else mac[:2]
    try:
        return bool(int(first, 16) & 0x02)
    except ValueError:
        return True


def _oui(mac: str) -> str:
    parts = (mac or "").lower().split(":")
    return ":".join(parts[:3]) if len(parts) >= 3 else ""


@dataclass(frozen=True)
class DeviceObservation:
    """What we observed about one device at one MAC. `ssids` is its PNL (networks
    it directed probes at); `ie_hash` is a hash of its probe-request IE template if
    a raw-frame tap captured one; `caps` are coarse capability tokens if available."""
    mac: str
    ssids: FrozenSet[str] = frozenset()
    ie_hash: str = ""
    caps: Tuple[str, ...] = ()
    ts: float = 0.0
    first_seen: Optional[float] = None


@dataclass(frozen=True)
class Fingerprint:
    key: str              # identity key — MAC-independent when linkable, else per-MAC
    linkable: bool        # can this tie DIFFERENT MACs to one device?
    strength: float       # 0..1 distinctiveness of the MAC-independent signal
    features: dict        # glass-box: what actually went in
    reason: str


def _hash(*parts) -> str:
    h = hashlib.sha256(repr(parts).encode()).hexdigest()
    return h[:16]


def fingerprint(obs: DeviceObservation) -> Fingerprint:
    """Compute a device identity from MAC-independent signals where possible."""
    randomized = is_randomized(obs.mac)
    pnl = tuple(sorted(s for s in obs.ssids if s))
    feats: dict = {}
    strength = 0.0
    link_parts: List[tuple] = []

    if obs.ie_hash:                     # strongest single signal
        link_parts.append(("ie", obs.ie_hash))
        feats["ie_hash"] = obs.ie_hash
        strength += 0.6
    if pnl:                             # the workhorse cross-MAC signal
        link_parts.append(("pnl", pnl))
        feats["pnl"] = list(pnl)
        strength += min(0.5, 0.15 * len(pnl))   # more networks = more distinctive
    if obs.caps and link_parts:         # caps only sharpen an existing link
        link_parts.append(("caps", tuple(obs.caps)))
        feats["caps"] = list(obs.caps)
        strength += 0.1

    if link_parts:
        key = "fp:" + _hash(*link_parts)
        strength = min(1.0, strength)
        reason = (f"linkable via {', '.join(k for k, _ in link_parts)} "
                  f"(strength {strength:.2f})")
        return Fingerprint(key=key, linkable=True, strength=strength,
                           features=feats, reason=reason)

    # no MAC-independent signal → cannot link across MAC rotation
    if not randomized:
        oui = _oui(obs.mac)
        feats["oui"] = oui
        return Fingerprint(key="mac:" + obs.mac.lower(), linkable=False, strength=0.0,
                           features=feats,
                           reason="stable (non-randomized) MAC, no PNL/IE — tracked by MAC, not linked")
    return Fingerprint(key="mac:" + obs.mac.lower(), linkable=False, strength=0.0,
                       features=feats,
                       reason="randomized MAC, no PNL/IE captured — cannot link across MAC changes")


@dataclass
class DeviceIdentity:
    key: str
    macs: Set[str] = field(default_factory=set)
    ssids: Set[str] = field(default_factory=set)
    linkable: bool = False
    strength: float = 0.0
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None
    observations: int = 0

    @property
    def mac_count(self) -> int:
        return len(self.macs)

    @property
    def reidentified(self) -> bool:
        """True when more than one MAC collapsed into this one device — i.e. we
        saw through MAC randomization."""
        return self.linkable and len(self.macs) > 1

    def to_dict(self) -> dict:
        return {
            "key": self.key, "macs": sorted(self.macs), "mac_count": self.mac_count,
            "ssids": sorted(self.ssids), "linkable": self.linkable,
            "strength": round(self.strength, 3), "reidentified": self.reidentified,
            "first_seen": self.first_seen, "last_seen": self.last_seen,
            "observations": self.observations,
        }


@dataclass
class DeviceLinker:
    """Clusters observations into device identities. Observations that share a
    MAC-independent fingerprint collapse into one identity even across different
    MACs; unlinkable ones stay per-MAC. Deterministic."""
    _by_key: Dict[str, DeviceIdentity] = field(default_factory=dict)

    def observe(self, obs: DeviceObservation) -> DeviceIdentity:
        fp = fingerprint(obs)
        ident = self._by_key.get(fp.key)
        if ident is None:
            ident = DeviceIdentity(key=fp.key, linkable=fp.linkable, strength=fp.strength)
            self._by_key[fp.key] = ident
        ident.macs.add((obs.mac or "").lower())
        ident.ssids.update(s for s in obs.ssids if s)
        ident.strength = max(ident.strength, fp.strength)
        ident.observations += 1
        ts = obs.ts
        fseen = obs.first_seen if obs.first_seen is not None else ts
        ident.first_seen = fseen if ident.first_seen is None else min(ident.first_seen, fseen)
        ident.last_seen = ts if ident.last_seen is None else max(ident.last_seen, ts)
        return ident

    def resolve(self, obs: DeviceObservation) -> str:
        return fingerprint(obs).key

    def identities(self) -> List[DeviceIdentity]:
        return sorted(self._by_key.values(),
                      key=lambda i: (not i.reidentified, -i.strength, -i.mac_count))

    def summary(self) -> dict:
        ids = list(self._by_key.values())
        reid = [i for i in ids if i.reidentified]
        linkable = [i for i in ids if i.linkable]
        collapsed = sum(i.mac_count for i in reid)
        return {
            "identities": len(ids),
            "linkable": len(linkable),
            "reidentified": len(reid),
            "macs_collapsed": collapsed,
            # MACs that re-id folded into devices: e.g. 7 MACs -> 2 devices
            "reidentified_mac_savings": collapsed - len(reid),
        }


# --- best-effort adapter from the sighting store ----------------------------- #

def observations_from_store(store, *, client_kinds=("wifi",)) -> List[DeviceObservation]:
    """Build observations from recorded sightings — one per MAC, with the set of
    SSIDs seen alongside it as a best-effort PNL. HONEST LIMITATION: today's event
    stream does not separate a client's directed probes from an AP's own beacon,
    and captures neither full PNL nor IE templates, so these observations are weak
    (often empty PNL → unlinkable). They get strong automatically once the
    probe-request / raw-frame tap feeds real PNL/IE here — the linker is unchanged.
    """
    sightings = store.query() if hasattr(store, "query") else []
    by_mac: Dict[str, Dict] = {}
    for s in sightings:
        if client_kinds and s.kind not in client_kinds:
            continue
        m = (s.mac or "").lower()
        if not m:
            continue
        rec = by_mac.setdefault(m, {"ssids": set(), "ts": s.ts, "first": s.ts})
        if s.ssid:
            rec["ssids"].add(s.ssid)
        rec["ts"] = max(rec["ts"], s.ts)
        rec["first"] = min(rec["first"], s.first_seen if s.first_seen is not None else s.ts)
    return [
        DeviceObservation(mac=m, ssids=frozenset(r["ssids"]), ts=r["ts"], first_seen=r["first"])
        for m, r in by_mac.items()
    ]


def link_store(store) -> DeviceLinker:
    linker = DeviceLinker()
    for obs in observations_from_store(store):
        linker.observe(obs)
    return linker
