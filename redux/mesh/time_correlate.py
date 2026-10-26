"""Swarm time-correlation — turn many nodes' clocks into joint intelligence.

The collector grid fuses nodes in SPACE (RSSI-weighted centroid). This fuses them
in TIME. Several Augur nodes each see fragments; correlating their timestamped
observations tells you things no single node knows:

  - corroboration : the same event seen by several nodes within a window is real,
                    not one node's glitch — confidence scales with node count
  - movement      : an entity seen at node A then node B is MOVING A→B; the dt and
                    direction fall out for free (the swarm as a tripwire line)
  - a merged timeline : every node's sightings of one entity on one clock

Nodes drift, so each node's timestamps are normalized by an optional per-node
clock offset before anything is compared — otherwise skew invents or hides
co-occurrence. A + B ⇒ C: A (per-node observations) + B (a common time base) ⇒
C (corroborated events, movement vectors, a swarm-wide timeline). Pure and
deterministic; no transport assumed (fed by LoRa/loopback/whatever the fleet uses).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


def _norm(entity: str) -> str:
    return (entity or "").strip().lower()


@dataclass(frozen=True)
class SwarmEvent:
    """One node's timestamped look at one entity."""
    node_id: str
    ts: float
    entity: str            # bssid / device key / target
    kind: str = "sighting"  # beacon|pmkid|deauth|probe|handshake|…
    rssi: Optional[int] = None


@dataclass(frozen=True)
class CorrelatedEvent:
    entity: str
    kind: str
    nodes: Tuple[str, ...]
    t_first: float
    t_last: float
    confidence: float      # 0..1, rises with distinct node count

    @property
    def span(self) -> float:
        return self.t_last - self.t_first

    def to_dict(self) -> dict:
        return {"entity": self.entity, "kind": self.kind, "nodes": list(self.nodes),
                "t_first": self.t_first, "t_last": self.t_last, "span": round(self.span, 3),
                "confidence": round(self.confidence, 3)}


@dataclass(frozen=True)
class Hop:
    entity: str
    from_node: str
    to_node: str
    t_from: float
    t_to: float

    @property
    def dt(self) -> float:
        return self.t_to - self.t_from

    def to_dict(self) -> dict:
        return {"entity": self.entity, "from": self.from_node, "to": self.to_node,
                "t_from": self.t_from, "t_to": self.t_to, "dt": round(self.dt, 3)}


@dataclass
class SwarmTimeCorrelator:
    """Correlate timestamped observations from multiple nodes on a common clock."""
    window: float = 5.0                               # co-occurrence window (seconds)
    skew: Dict[str, float] = field(default_factory=dict)   # per-node clock offset
    _events: List[SwarmEvent] = field(default_factory=list)

    def add(self, event: SwarmEvent) -> None:
        self._events.append(event)

    def add_many(self, events) -> None:
        self._events.extend(events or ())

    def set_skew(self, node_id: str, offset: float) -> None:
        """Node clock offset: adjusted_ts = ts - offset (normalize to a common base)."""
        self.skew[node_id] = float(offset)

    def _t(self, e: SwarmEvent) -> float:
        return e.ts - self.skew.get(e.node_id, 0.0)

    def nodes(self) -> List[str]:
        return sorted({e.node_id for e in self._events})

    def _by_entity_kind(self) -> Dict[Tuple[str, str], List[SwarmEvent]]:
        groups: Dict[Tuple[str, str], List[SwarmEvent]] = {}
        for e in self._events:
            groups.setdefault((_norm(e.entity), e.kind), []).append(e)
        return groups

    def corroborated(self, *, kind: Optional[str] = None, min_nodes: int = 2) -> List[CorrelatedEvent]:
        """Events of the same entity+kind seen by >= min_nodes DISTINCT nodes inside
        one time window. Confidence rises with node count (capped at 1.0)."""
        out: List[CorrelatedEvent] = []
        total_nodes = max(1, len(self.nodes()))
        for (entity, ekind), evs in self._by_entity_kind().items():
            if kind is not None and ekind != kind:
                continue
            evs = sorted(evs, key=self._t)
            # sliding window cluster on adjusted time
            i = 0
            n = len(evs)
            while i < n:
                j = i
                cluster = [evs[i]]
                while j + 1 < n and self._t(evs[j + 1]) - self._t(cluster[0]) <= self.window:
                    j += 1
                    cluster.append(evs[j])
                node_set = {e.node_id for e in cluster}
                if len(node_set) >= min_nodes:
                    ts = [self._t(e) for e in cluster]
                    out.append(CorrelatedEvent(
                        entity=entity, kind=ekind, nodes=tuple(sorted(node_set)),
                        t_first=min(ts), t_last=max(ts),
                        confidence=min(1.0, len(node_set) / total_nodes)))
                i = j + 1
        out.sort(key=lambda c: (-c.confidence, c.t_first, c.entity))
        return out

    def movements(self, entity: str) -> List[Hop]:
        """Node-to-node hops for one entity over time: consecutive observations from
        DIFFERENT nodes are a move from the first node's area to the next's."""
        evs = sorted((e for e in self._events if _norm(e.entity) == _norm(entity)),
                     key=self._t)
        hops: List[Hop] = []
        for a, b in zip(evs, evs[1:]):
            if a.node_id != b.node_id:
                hops.append(Hop(entity=_norm(entity), from_node=a.node_id, to_node=b.node_id,
                                t_from=self._t(a), t_to=self._t(b)))
        return hops

    def timeline(self, entity: str) -> List[dict]:
        """Every node's sightings of one entity, merged onto the common clock."""
        evs = sorted((e for e in self._events if _norm(e.entity) == _norm(entity)),
                     key=self._t)
        return [{"t": self._t(e), "node": e.node_id, "kind": e.kind, "rssi": e.rssi}
                for e in evs]

    def summary(self) -> dict:
        corr = self.corroborated()
        entities = {_norm(e.entity) for e in self._events}
        movers = [ent for ent in entities if self.movements(ent)]
        return {
            "nodes": len(self.nodes()),
            "events": len(self._events),
            "entities": len(entities),
            "corroborated": len(corr),
            "moving": len(movers),
            "note": (f"{len(corr)} corroborated event(s) across {len(self.nodes())} node(s); "
                     f"{len(movers)} entity(ies) tracked moving between nodes"
                     if self._events else "no swarm observations yet"),
        }
