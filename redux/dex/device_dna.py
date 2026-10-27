"""Device DNA — behavioral re-identification past MAC randomization.

The fingerprint layer links MACs that share an *exact* signal (the same PNL or
the same IE template hash). But a modern phone rotates its MAC AND often probes
with an empty PNL, so there's nothing exact to match. Device DNA closes that gap:
it builds a MAC-independent *behavioral* signature — how a device probes, not what
it says — and links MACs by SIMILARITY rather than equality.

The DNA vector (all MAC-independent):
  - cadence   : the typical gap between a device's probe bursts, bucketed
  - burstiness: how many frames it tends to send per burst
  - channels  : the set of channels/bands it probes on
  - rates     : the supported-rate set it advertises (stack/driver tell)
  - ie_hash   : the probe-request IE template, if a raw tap caught one

Two MACs collapse into one device when their DNA similarity clears a threshold.
This is the same signal the unwanted-tracker check uses — turned around: track a
target across its MAC rotation in the lab, or notice a device trailing you.

Glass-box and conservative: every link states which components matched and how
strongly, it never links on a MAC-derived signal, and a thin/low-strength DNA is
reported as unlinkable rather than forced into a guess. Pure/deterministic.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from .fingerprint import is_randomized

# cadence buckets (seconds) — probe schedules cluster around these; a device is
# matched to the nearest bucket so small timing noise doesn't split an identity.
_CADENCE_BUCKETS = (1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0)


def _nearest_bucket(value: float) -> float:
    return min(_CADENCE_BUCKETS, key=lambda b: abs(b - value))


def _median_interarrival(timestamps: List[float]) -> Optional[float]:
    ts = sorted(t for t in (timestamps or []) if t is not None)
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b - a > 0.001]
    return statistics.median(gaps) if gaps else None


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 0.0
    u = a | b
    return len(a & b) / len(u) if u else 0.0


@dataclass(frozen=True)
class BehaviorTrace:
    """Everything observed about ONE MAC's probing behavior over a capture window."""
    mac: str
    timestamps: Tuple[float, ...] = ()        # probe-burst times for this MAC
    ie_hash: str = ""
    rates: Tuple[int, ...] = ()               # advertised supported rates
    channels: Tuple[int, ...] = ()            # channels it was heard probing on
    ssids: FrozenSet[str] = frozenset()       # PNL, if any (kept for cross-check)
    burst_sizes: Tuple[int, ...] = ()         # frames per burst, if the tap grouped them


@dataclass(frozen=True)
class DeviceDNA:
    """A MAC-independent behavioral signature + how distinctive it is (0..1)."""
    cadence: Optional[float]
    burstiness: Optional[float]
    channels: FrozenSet[int]
    rates: Tuple[int, ...]
    ie_hash: str
    strength: float
    features: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"cadence": self.cadence, "burstiness": self.burstiness,
                "channels": sorted(self.channels), "rates": list(self.rates),
                "ie_hash": self.ie_hash, "strength": round(self.strength, 3),
                "features": self.features}


def device_dna(trace: BehaviorTrace) -> DeviceDNA:
    """Compute the behavioral DNA of one MAC's trace. Strength rises with how many
    independent, distinctive components are present."""
    feats: dict = {}
    strength = 0.0

    cadence = None
    med = _median_interarrival(list(trace.timestamps))
    if med is not None:
        cadence = _nearest_bucket(med)
        feats["cadence_s"] = cadence
        strength += 0.2

    burstiness = None
    if trace.burst_sizes:
        burstiness = float(statistics.median(trace.burst_sizes))
        feats["burstiness"] = burstiness
        strength += 0.1

    channels = frozenset(c for c in trace.channels if c)
    if channels:
        feats["channels"] = sorted(channels)
        strength += min(0.15, 0.05 * len(channels))

    rates = tuple(trace.rates)
    if rates:
        feats["rates"] = list(rates)
        strength += 0.2                 # the rate set is a solid stack/driver tell

    ie_hash = trace.ie_hash or ""
    if ie_hash:
        feats["ie_hash"] = ie_hash
        strength += 0.5                 # strongest single structural signal

    return DeviceDNA(cadence=cadence, burstiness=burstiness, channels=channels,
                     rates=rates, ie_hash=ie_hash, strength=min(1.0, strength),
                     features=feats)


def dna_similarity(a: DeviceDNA, b: DeviceDNA) -> Tuple[float, List[str]]:
    """How alike two DNA vectors are (0..1) + which components matched. Weighted so
    a shared IE template dominates and timing alone can never carry a link."""
    score = 0.0
    matched: List[str] = []
    if a.ie_hash and a.ie_hash == b.ie_hash:
        score += 0.5
        matched.append("ie")
    if a.rates and a.rates == b.rates:
        score += 0.2
        matched.append("rates")
    if a.cadence is not None and b.cadence is not None:
        # within one bucket step counts as a cadence match
        ai, bi = _CADENCE_BUCKETS.index(a.cadence), _CADENCE_BUCKETS.index(b.cadence)
        if abs(ai - bi) <= 1:
            score += 0.15
            matched.append("cadence")
    if a.channels and b.channels:
        j = _jaccard(a.channels, b.channels)
        if j > 0:
            score += 0.15 * j
            matched.append("channels")
    if (a.burstiness is not None and b.burstiness is not None
            and abs(a.burstiness - b.burstiness) <= 2):
        score += 0.05
        matched.append("burst")
    return min(1.0, score), matched


@dataclass
class DnaIdentity:
    key: str
    macs: List[str] = field(default_factory=list)
    dna: Optional[DeviceDNA] = None
    matched_on: Tuple[str, ...] = ()
    confidence: float = 0.0

    @property
    def reidentified(self) -> bool:
        return len(self.macs) > 1

    def to_dict(self) -> dict:
        return {"key": self.key, "macs": sorted(self.macs),
                "reidentified": self.reidentified,
                "matched_on": list(self.matched_on),
                "confidence": round(self.confidence, 3),
                "dna": self.dna.to_dict() if self.dna else None}


@dataclass
class DnaLinker:
    """Cluster MACs into devices by behavioral-DNA similarity. Conservative: a DNA
    below `min_strength` is never merged, and a merge needs similarity >= `threshold`."""
    threshold: float = 0.55
    min_strength: float = 0.4
    _clusters: List[dict] = field(default_factory=list)   # {dna, macs, matched}

    def add(self, trace: BehaviorTrace) -> None:
        dna = device_dna(trace)
        mac = (trace.mac or "").lower()
        # a device with a stable (non-random) MAC and no strong behavioral signal is
        # tracked by its MAC, not fuzzy-linked — don't risk a false merge.
        if dna.strength < self.min_strength:
            self._clusters.append({"dna": dna, "macs": [mac], "matched": (),
                                   "conf": 0.0, "weak": True})
            return
        best = None
        best_sim = 0.0
        best_matched: List[str] = []
        for c in self._clusters:
            if c.get("weak"):
                continue
            sim, matched = dna_similarity(dna, c["dna"])
            if sim > best_sim:
                best, best_sim, best_matched = c, sim, matched
        if best is not None and best_sim >= self.threshold:
            if mac not in best["macs"]:
                best["macs"].append(mac)
            best["matched"] = tuple(sorted(set(best["matched"]) | set(best_matched)))
            best["conf"] = max(best["conf"], best_sim)
        else:
            self._clusters.append({"dna": dna, "macs": [mac], "matched": (), "conf": dna.strength})

    def identities(self) -> List[DnaIdentity]:
        out: List[DnaIdentity] = []
        for i, c in enumerate(sorted(self._clusters, key=lambda c: sorted(c["macs"]))):
            key = ("dna:" + c["dna"].ie_hash[:12]) if c["dna"].ie_hash else f"dna:{i}:{sorted(c['macs'])[0]}"
            out.append(DnaIdentity(key=key, macs=list(c["macs"]), dna=c["dna"],
                                   matched_on=tuple(c["matched"]), confidence=c["conf"]))
        # reidentified devices first (the interesting ones), then by MAC count
        out.sort(key=lambda d: (not d.reidentified, -len(d.macs), d.key))
        return out

    def summary(self) -> dict:
        ids = self.identities()
        reid = [d for d in ids if d.reidentified]
        return {
            "macs_seen": sum(len(c["macs"]) for c in self._clusters),
            "devices": len(ids),
            "reidentified": len(reid),
            "note": (f"{len(reid)} device(s) re-identified across MAC rotation by behavior"
                     if reid else "no cross-MAC behavioral links yet"),
        }
