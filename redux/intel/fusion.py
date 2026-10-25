"""Co-occurrence fusion — devices that travel together are probably one owner.

The sighting store collapses to one row per device, so it can't show that two
devices move together over time. The observation log (redux.geo.follower,
`observations.jsonl`) is the append-only TIME SERIES that can: it records located
observations (mac, ts, lat, lon). This clusters the MACs in that log by how often
they appear in the SAME time+place window — the signature of being carried
together (a phone + watch + earbuds, or a tracker shadowing you).

It is radio-agnostic: it fuses whatever the log contains. Today the daemon logs
BLE there, so this clusters BLE devices that travel together (counter-surveillance:
"this tag is always with me"). The moment WiFi observations are also logged, the
very same algorithm becomes true cross-radio WiFi×BLE fusion — no code change, it
just gets more to join. (Enabling that WiFi feed is a separate, deliberate
daemon change: it raises write volume on the live path, so it's not bundled here.)

Honest about its evidence: co-presence is correlation, not proof — two strangers
riding the same bus co-occur too. So links need REPEATED co-occurrence (min_co)
and a minimum overlap ratio (Jaccard), every group carries its evidence counts and
a graded confidence, and nothing is asserted as one identity. Pure/deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple


@dataclass(frozen=True)
class FusionGroup:
    macs: Tuple[str, ...]          # devices that repeatedly co-occur
    co_windows: int                # min pairwise shared (time,place) windows in the group
    score: float                   # min pairwise Jaccard overlap in the group (0..1)
    confidence: str                # strong | medium | weak
    windows_seen: int              # distinct windows any member appeared in
    summary: str


def _bucket(ts: float, lat, lon, window_s: float, precision: int):
    from ..geo.geohash import encode as _gh
    cell = "nogeo"
    if lat is not None and lon is not None:
        try:
            cell = _gh(float(lat), float(lon), precision)
        except (TypeError, ValueError):
            cell = "nogeo"
    try:
        slot = int(float(ts) // window_s)
    except (TypeError, ValueError):
        slot = 0
    return (slot, cell)


def _confidence(co: int, score: float) -> str:
    if co >= 5 and score >= 0.6:
        return "strong"
    if co >= 3 and score >= 0.4:
        return "medium"
    return "weak"


def fuse(observations: Sequence, *, window_s: float = 120.0, precision: int = 7,
         min_co: int = 2, min_score: float = 0.3) -> List[FusionGroup]:
    """Cluster MACs that repeatedly share a (time-window, place-cell) bucket.

    `observations`: objects/dicts with mac, ts, lat, lon (ObservationLog.load()).
    A pair links when it shares >= `min_co` buckets AND their bucket-set Jaccard
    overlap is >= `min_score`; union-find groups the links. Returns groups of >=2."""
    buckets_by_mac: Dict[str, Set[tuple]] = {}
    for o in observations:
        mac = (getattr(o, "mac", None) if not isinstance(o, dict) else o.get("mac")) or ""
        if not mac:
            continue
        ts = getattr(o, "ts", None) if not isinstance(o, dict) else o.get("ts")
        lat = getattr(o, "lat", None) if not isinstance(o, dict) else o.get("lat")
        lon = getattr(o, "lon", None) if not isinstance(o, dict) else o.get("lon")
        buckets_by_mac.setdefault(mac.lower(), set()).add(_bucket(ts, lat, lon, window_s, precision))

    macs = sorted(buckets_by_mac)
    n = len(macs)
    if n < 2:
        return []

    parent = {m: m for m in macs}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    pair_stats: Dict[frozenset, Tuple[int, float]] = {}
    for i in range(n):
        bi = buckets_by_mac[macs[i]]
        for j in range(i + 1, n):
            bj = buckets_by_mac[macs[j]]
            co = len(bi & bj)
            if co < min_co:
                continue
            score = co / len(bi | bj)
            if score < min_score:
                continue
            pair_stats[frozenset((macs[i], macs[j]))] = (co, score)
            ra, rb = find(macs[i]), find(macs[j])
            if ra != rb:
                parent[ra] = rb

    groups: Dict[str, Set[str]] = {}
    for m in macs:
        groups.setdefault(find(m), set()).add(m)

    out: List[FusionGroup] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        member_list = tuple(sorted(members))
        internal = [pair_stats[p] for p in pair_stats if p <= members]
        if not internal:
            continue
        min_co_v = min(c for c, _ in internal)
        min_score_v = min(s for _, s in internal)
        windows_seen = len(set().union(*(buckets_by_mac[m] for m in member_list)))
        conf = _confidence(min_co_v, min_score_v)
        out.append(FusionGroup(
            macs=member_list, co_windows=min_co_v, score=round(min_score_v, 3),
            confidence=conf, windows_seen=windows_seen,
            summary=(f"{len(member_list)} devices travel together — shared {min_co_v}+ "
                     f"time/place window(s), overlap {min_score_v:.0%} [{conf}]")))
    out.sort(key=lambda g: (-len(g.macs), -g.co_windows, -g.score))
    return out
