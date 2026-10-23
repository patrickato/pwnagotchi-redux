"""Collector grid — fuse one target's RSSI across several nodes at once (M-2/M-9).

A single wardriving node locates a transmitter from its *own* successive
positions as it moves (geo.estimate). A collector grid instead has several nodes
observing the same BSSID from different fixed positions at the same time — that
spatial diversity pins the target far better than any one node, and it is the
heart of the multi-device swarm.

This is the fusion engine: feed it per-node observations (each carries the
observing node's own position + the RSSI it heard), and it fuses the nodes'
strongest look at a target into one glass-box LocationEstimate. It is honest
about method — an RSSI-weighted centroid across nodes, not calibrated RF
trilateration — and fails closed (returns a reason, no estimate) when fewer than
`min_nodes` distinct nodes have reported.

Passive by nature: an observation is a received RSSI of an AP (a sighting), so —
like SightingStore — this never transmits and is not a firing path, hence not
scope-gated. The transport that carries observations between nodes mirrors
scope_sync (signed, injected); this module is transport-agnostic so it stays
testable with no radio.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from redux.geo.estimate import LocationEstimate, estimate_from_coords


def _norm_mac(mac: str) -> str:
    return mac.strip().lower()


@dataclass(frozen=True)
class NodeObservation:
    """One node's look at one target: the node's own position + the RSSI it heard."""
    node_id: str
    node_lat: float
    node_lon: float
    bssid: str
    rssi: int
    ts: float = 0.0


class CollectorGrid:
    """Accumulates per-node observations and fuses them per target. In-memory;
    fed by whatever transport the fleet uses (LoRa/loopback), so it is pure and
    testable."""

    def __init__(self) -> None:
        # bssid -> node_id -> strongest observation from that node
        self._by_target: Dict[str, Dict[str, NodeObservation]] = {}

    def add_observation(self, node_id: str, node_lat: float, node_lon: float,
                        bssid: str, rssi: int, *, ts: float = 0.0) -> NodeObservation:
        """Record a node's look at a target. Keeps that node's STRONGEST sample
        (its closest approach), so a chatty node can't dominate with repeats."""
        obs = NodeObservation(node_id=node_id, node_lat=float(node_lat),
                              node_lon=float(node_lon), bssid=_norm_mac(bssid),
                              rssi=int(rssi), ts=float(ts))
        per_node = self._by_target.setdefault(obs.bssid, {})
        prev = per_node.get(node_id)
        if prev is None or obs.rssi > prev.rssi:   # stronger == closer
            per_node[node_id] = obs
        return obs

    def nodes(self) -> List[str]:
        seen = set()
        for per_node in self._by_target.values():
            seen.update(per_node)
        return sorted(seen)

    def targets(self) -> List[str]:
        return sorted(self._by_target)

    def observations(self, bssid: str) -> List[NodeObservation]:
        return sorted(self._by_target.get(_norm_mac(bssid), {}).values(),
                      key=lambda o: o.node_id)

    def fuse(self, bssid: str, *, min_nodes: int = 2,
             ) -> Tuple[Optional[LocationEstimate], str]:
        """Fuse all contributing nodes' strongest observations of `bssid` into one
        estimate. Requires >= min_nodes DISTINCT nodes (the cross-node point);
        fails closed with a glass-box reason otherwise."""
        obs = self.observations(bssid)
        if len(obs) < min_nodes:
            return None, (f"refused: {len(obs)} node(s) have observed {_norm_mac(bssid)} — "
                          f"need {min_nodes} distinct collector nodes to fuse")
        points = [(o.node_lat, o.node_lon, o.rssi) for o in obs]
        est = estimate_from_coords(points, min_observations=min_nodes)
        if est is None:
            return None, (f"refused: {len(obs)} node observations of {_norm_mac(bssid)} "
                          f"lacked usable coordinates/RSSI")
        who = ", ".join(o.node_id for o in obs)
        reason = (f"fused {len(obs)} collector nodes ({who}) for {_norm_mac(bssid)}: "
                  f"{est.reason}")
        return est, reason

    def fuse_all(self, *, min_nodes: int = 2,
                 ) -> Dict[str, Tuple[Optional[LocationEstimate], str]]:
        """Fuse every target currently observed; targets below the node threshold
        are included with a (None, reason) so the caller sees why."""
        return {b: self.fuse(b, min_nodes=min_nodes) for b in self.targets()}
