"""Entity graph — the whole cage as one picture.

The payoff of every fusion piece. It unifies what the box knows about physical
devices into a single graph:

  * NODES are physical devices. reid (shared distinctive probe PNLs) MERGES a
    device's randomized MACs into one node — that's strong *identity*.
  * EDGES are "travels-with" links. Co-occurrence fusion (devices repeatedly in
    the same time+place) is *association*, NOT identity — it could be one
    device's second radio or a companion device/person — so it's an edge
    between nodes, never a merge. Honest about the difference.
  * Each node is annotated by joining its MACs to the sighting store
    (where/when/signal), OUI (vendor, only for non-randomized MACs), and the
    loot vault (was its key recovered — redacted).

Pure/deterministic: all inputs are passed in (reid entities, fusion groups,
sightings indexed by MAC, recovered-key set, vendor lookup), so the graph is
testable; the CLI wires the live stores. Read-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple


@dataclass(frozen=True)
class EntityNode:
    id: str
    macs: Tuple[str, ...]
    randomized: int
    vendor: Optional[str]
    sightings: int
    last_seen: Optional[float]
    strongest_rssi: Optional[int]
    places: Tuple[str, ...]
    cracked: bool
    linked_by: Tuple[str, ...]        # how this device's MACs were tied together (e.g. "pnl")
    summary: str


@dataclass(frozen=True)
class EntityEdge:
    a: str                            # node id
    b: str                            # node id
    relation: str                     # "travels-with"
    confidence: str
    evidence: int                     # shared co-occurrence windows


@dataclass(frozen=True)
class EntityGraph:
    nodes: Tuple[EntityNode, ...]
    edges: Tuple[EntityEdge, ...]
    summary: str


def _annotate(macs, sightings_by_mac, vendor_lookup):
    from ..geo.geohash import encode as _gh
    from ..geo.mac_normalize import is_locally_administered
    sights = []
    for m in macs:
        sights.extend(sightings_by_mac.get(m.lower(), []))
    tss = [s.ts for s in sights if getattr(s, "ts", None) is not None]
    rssis = [s.rssi for s in sights if getattr(s, "rssi", None) is not None]
    places = set()
    for s in sights:
        lat, lon = getattr(s, "lat", None), getattr(s, "lon", None)
        if lat is not None and lon is not None:
            try:
                places.add(_gh(float(lat), float(lon), 7))
            except (TypeError, ValueError):
                pass
    vendor = None
    if vendor_lookup:
        for m in macs:
            if not is_locally_administered(m):
                v = vendor_lookup(m)
                if v:
                    vendor = v
                    break
    randomized = sum(1 for m in macs if is_locally_administered(m))
    return (len(sights), (max(tss) if tss else None), (max(rssis) if rssis else None),
            tuple(sorted(places)), vendor, randomized)


def build_graph(*, reid_entities: Sequence = (), fusion_groups: Sequence = (),
                sightings_by_mac: Optional[Dict[str, list]] = None,
                cracked_macs: Optional[Set[str]] = None, vendor_lookup=None,
                extra_macs: Sequence[str] = ()) -> EntityGraph:
    """Compose the graph. reid entities -> device nodes; fusion groups -> travels-with
    edges; sightings/OUI/loot -> node annotations."""
    sightings_by_mac = sightings_by_mac or {}
    cracked = {m.lower() for m in (cracked_macs or set())}

    # 1. nodes: one per reid entity (merged identity); singletons for any other MAC seen
    node_of_mac: Dict[str, str] = {}
    node_macs: Dict[str, Tuple[str, ...]] = {}
    node_linked: Dict[str, Tuple[str, ...]] = {}
    for e in reid_entities:
        macs = tuple(sorted(m.lower() for m in e.macs))
        nid = "dev:" + macs[0]
        node_macs[nid] = macs
        node_linked[nid] = ("pnl",)
        for m in macs:
            node_of_mac[m] = nid
    # standalone MACs (sighted / in fusion / explicitly listed) not already in a node
    seen_macs = set(sightings_by_mac) | {m.lower() for g in fusion_groups for m in g.macs} | {m.lower() for m in extra_macs}
    for m in sorted(seen_macs):
        if m not in node_of_mac:
            nid = "dev:" + m
            node_macs[nid] = (m,)
            node_linked[nid] = ()
            node_of_mac[m] = nid

    # 2. nodes annotated
    nodes: List[EntityNode] = []
    for nid, macs in sorted(node_macs.items()):
        n_sight, last_seen, strongest, places, vendor, rnd = _annotate(macs, sightings_by_mac, vendor_lookup)
        cracked_here = any(m in cracked for m in macs)
        bits = [f"{len(macs)} MAC(s)", vendor or ("randomized — vendor unknown" if rnd == len(macs) else "vendor unknown")]
        if n_sight:
            bits.append(f"seen {n_sight}×")
            if strongest is not None:
                bits.append(f"{strongest} dBm")
            if places:
                bits.append(f"{len(places)} place(s)")
        if cracked_here:
            bits.append("KEY RECOVERED")
        nodes.append(EntityNode(
            id=nid, macs=macs, randomized=rnd, vendor=vendor, sightings=n_sight,
            last_seen=last_seen, strongest_rssi=strongest, places=places,
            cracked=cracked_here, linked_by=node_linked[nid], summary=" · ".join(bits)))

    # 3. edges: fusion co-travel between DIFFERENT nodes = travels-with
    edges: List[EntityEdge] = []
    seen_pairs: Set[frozenset] = set()
    for g in fusion_groups:
        involved = sorted({node_of_mac.get(m.lower(), "dev:" + m.lower()) for m in g.macs})
        for i in range(len(involved)):
            for j in range(i + 1, len(involved)):
                pair = frozenset((involved[i], involved[j]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                edges.append(EntityEdge(a=involved[i], b=involved[j], relation="travels-with",
                                        confidence=getattr(g, "confidence", "weak"),
                                        evidence=getattr(g, "co_windows", 0)))
    edges.sort(key=lambda e: (e.a, e.b))
    nodes.sort(key=lambda n: (-n.sightings, -len(n.macs), n.id))
    summary = (f"{len(nodes)} device(s), {len(edges)} travels-with link(s); "
               f"{sum(1 for n in nodes if n.cracked)} with a recovered key")
    return EntityGraph(nodes=tuple(nodes), edges=tuple(edges), summary=summary)
