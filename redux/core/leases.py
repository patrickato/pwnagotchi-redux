"""Resource ownership / lease layer — the keystone that stops clobbering.

The capability graph answers "what can the box do?"; this answers "who holds the
actual radio right now?". The physical resources — each Wi-Fi radio, the BT
adapter, an SDR, GPS, a GPIO line — can't be used by two things at once in
conflicting modes. Without arbitration, bettercap and AngryOxide fight over
wlan1, CSI sensing steals the onboard radio mid-capture, a swarm node and a local
engine both claim the same adapter. This makes ownership observable and
revocable so swarm + multi-engine + multi-band coexist on purpose, not by luck.

Model: an owner (an engine, a job, a swarm node) acquires a LEASE on a resource,
exclusive or shared, with a priority and a reason. The broker grants, denies, or
PREEMPTS (a higher-priority owner can take an exclusive resource, and the loser is
told why). Leases can carry a TTL so a crashed owner's hold lapses instead of
wedging the radio forever. Every decision is glass-box: granted/denied/preempted
all carry a human-readable reason. Pure/deterministic over an injected clock.

This is the arbitration the kill-chain (#1), multi-band RF (#3), sensing (#5) and
swarm (#11) all build on.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

EXCLUSIVE = "exclusive"
SHARED = "shared"


@dataclass(frozen=True)
class Resource:
    """A physical resource the box owns."""
    name: str                 # "wlan1", "hci0", "sdr0", "gps0", "gpio:18"
    rtype: str                # wifi | bt | sdr | gps | gpio
    shareable: bool = False   # may several owners hold it at once (e.g. GPS read-only)


@dataclass(frozen=True)
class Lease:
    resource: str
    owner: str
    mode: str                 # EXCLUSIVE | SHARED
    priority: int             # higher wins a preemption contest
    reason: str
    granted_at: float
    expires: Optional[float] = None   # None = no TTL

    def active_at(self, now: float) -> bool:
        return self.expires is None or now < self.expires

    def to_dict(self) -> dict:
        return {"resource": self.resource, "owner": self.owner, "mode": self.mode,
                "priority": self.priority, "reason": self.reason,
                "granted_at": self.granted_at, "expires": self.expires}


@dataclass(frozen=True)
class Grant:
    ok: bool
    lease: Optional[Lease]
    reason: str
    preempted: Tuple[Lease, ...] = ()

    def to_dict(self) -> dict:
        return {"ok": self.ok, "lease": self.lease.to_dict() if self.lease else None,
                "reason": self.reason, "preempted": [l.to_dict() for l in self.preempted]}


@dataclass
class ResourceBroker:
    """Grants, denies, preempts and releases leases over registered resources."""
    clock: Callable[[], float] = time.time
    _resources: Dict[str, Resource] = field(default_factory=dict)
    _leases: List[Lease] = field(default_factory=list)
    _revocations: List[dict] = field(default_factory=list)   # audit of forced revokes

    # ---- registration -------------------------------------------------------
    def register(self, resource: Resource) -> "ResourceBroker":
        self._resources[resource.name] = resource
        return self

    def resource(self, name: str) -> Optional[Resource]:
        return self._resources.get(name)

    # ---- state --------------------------------------------------------------
    def _prune(self, now: float) -> None:
        self._leases = [l for l in self._leases if l.active_at(now)]

    def holders(self, resource: str, *, now: Optional[float] = None) -> List[Lease]:
        now = self.clock() if now is None else now
        self._prune(now)
        return [l for l in self._leases if l.resource == resource]

    def active(self, *, now: Optional[float] = None) -> List[Lease]:
        now = self.clock() if now is None else now
        self._prune(now)
        return list(self._leases)

    # ---- arbitration --------------------------------------------------------
    def can_acquire(self, resource: str, owner: str, *, mode: str = EXCLUSIVE,
                    priority: int = 0, now: Optional[float] = None) -> Tuple[bool, str, List[Lease]]:
        """Would this request be granted? Returns (ok, reason, conflicts_that_would_be_preempted)."""
        now = self.clock() if now is None else now
        res = self._resources.get(resource)
        if res is None:
            return False, f"no such resource {resource!r} (register it first)", []
        held = [l for l in self.holders(resource, now=now) if l.owner != owner]
        if not held:
            return True, "free", []
        if mode == SHARED and res.shareable and all(h.mode == SHARED for h in held):
            return True, f"shared with {len(held)} other holder(s)", []
        # a conflict exists — can we preempt? only strictly-lower-priority holders.
        blockers = [h for h in held if h.priority >= priority]
        if blockers:
            top = max(blockers, key=lambda h: h.priority)
            return (False,
                    f"held by {top.owner!r} (priority {top.priority} >= yours {priority}) "
                    f"for: {top.reason}", [])
        # every holder is lower priority → they would be preempted
        return True, f"preempt {len(held)} lower-priority holder(s)", held

    def acquire(self, resource: str, owner: str, *, mode: str = EXCLUSIVE,
                priority: int = 0, reason: str = "", ttl: Optional[float] = None,
                now: Optional[float] = None) -> Grant:
        """Acquire (or renew) a lease. Preempts strictly-lower-priority exclusive
        holders; denied if an equal/higher holder blocks. Glass-box."""
        now = self.clock() if now is None else now
        ok, why, preempt = self.can_acquire(resource, owner, mode=mode,
                                             priority=priority, now=now)
        if not ok:
            return Grant(False, None, f"denied: {why}")
        # drop preempted holders (record the revocation for observability)
        for p in preempt:
            self._revocations.append({"resource": resource, "evicted": p.owner,
                                      "by": owner, "reason": f"preempted by higher priority {priority}",
                                      "at": now})
        self._leases = [l for l in self._leases
                        if not (l.resource == resource and
                                (l in preempt or l.owner == owner))]   # replace own prior lease
        lease = Lease(resource=resource, owner=owner, mode=mode, priority=priority,
                      reason=reason or "(unspecified)", granted_at=now,
                      expires=(now + ttl) if ttl else None)
        self._leases.append(lease)
        verb = "granted" if not preempt else f"granted (preempted {len(preempt)})"
        return Grant(True, lease, f"{verb}: {why}", tuple(preempt))

    def release(self, resource: str, owner: str) -> bool:
        """Owner voluntarily gives up its lease. Returns whether one was held."""
        before = len(self._leases)
        self._leases = [l for l in self._leases
                        if not (l.resource == resource and l.owner == owner)]
        return len(self._leases) < before

    def revoke(self, resource: str, owner: str, *, reason: str = "",
               now: Optional[float] = None) -> bool:
        """Admin/supervisor force-revoke (e.g. a hung owner). Audited."""
        now = self.clock() if now is None else now
        if self.release(resource, owner):
            self._revocations.append({"resource": resource, "evicted": owner,
                                      "by": "supervisor", "reason": reason or "forced",
                                      "at": now})
            return True
        return False

    def owner_of(self, resource: str, *, now: Optional[float] = None) -> Optional[str]:
        held = self.holders(resource, now=now)
        excl = [h for h in held if h.mode == EXCLUSIVE]
        return excl[0].owner if excl else (held[0].owner if held else None)

    # ---- observability ------------------------------------------------------
    def snapshot(self, *, now: Optional[float] = None) -> dict:
        """The observable ownership map for the status model / UIs."""
        now = self.clock() if now is None else now
        self._prune(now)
        rows = []
        for name, res in sorted(self._resources.items()):
            held = [l for l in self._leases if l.resource == name]
            rows.append({"resource": name, "type": res.rtype, "shareable": res.shareable,
                         "holders": [{"owner": l.owner, "mode": l.mode,
                                      "priority": l.priority, "reason": l.reason,
                                      "expires": l.expires} for l in held],
                         "free": not held})
        return {"resources": rows,
                "leased": sum(1 for r in rows if not r["free"]),
                "free": sum(1 for r in rows if r["free"]),
                "recent_revocations": self._revocations[-10:],
                "note": f"{sum(1 for r in rows if not r['free'])}/{len(rows)} resource(s) leased"}
