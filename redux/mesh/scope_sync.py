"""Distributed Scope sync over the mesh — arm once, propagate to the swarm.

Arm a target on one node and it propagates to every node over the LoRa/Meshtastic
lane, so a multi-device engagement shares one authorization set. The headline is
not the propagation — it's that the propagation is **authenticated**. A scope
delta that isn't signed with the swarm's shared key is **rejected**, because a
sync that any radio in range could inject targets into would be worse than no sync
at all: it would let someone arm your fleet at a target you never chose. That is
the aiming-integrity model extended to the swarm.

Design:
  - Every change emits a compact `ScopeDelta` (op/kind/value/job/expires/ts/origin)
    wrapped in a `SignedMessage` (HMAC-SHA256 over canonical JSON with the swarm
    key). Fits a LoRa frame.
  - `apply()` verifies the signature first and refuses anything that fails —
    glass-box reason, scope untouched. Then it merges last-writer-wins by `ts`, so
    out-of-order LoRa delivery converges and a stale delta can't resurrect a
    removed target.
  - Expiry rides along, so lapsed authorization stops authorizing everywhere.

Pure/testable: the transport (the real LoRa send/recv) is injected; the tests use
an in-memory loopback to prove convergence AND forgery rejection with no radio.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ScopeDelta:
    op: str                 # "add" | "remove"
    kind: str               # bssid | ssid | cidr
    value: str
    job: str = ""
    expires: Optional[float] = None
    ts: float = 0.0
    origin: str = ""        # node id that produced it

    def payload(self) -> dict:
        return {"op": self.op, "kind": self.kind, "value": self.value, "job": self.job,
                "expires": self.expires, "ts": self.ts, "origin": self.origin}


def _canon(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def sign(payload: dict, key: bytes) -> str:
    return hmac.new(key, _canon(payload), hashlib.sha256).hexdigest()


def verify(payload: dict, sig: str, key: bytes) -> bool:
    return hmac.compare_digest(sign(payload, key), sig or "")


@dataclass(frozen=True)
class SignedMessage:
    payload: dict
    sig: str

    def to_json(self) -> str:
        return json.dumps({"payload": self.payload, "sig": self.sig})

    @classmethod
    def from_json(cls, s: str) -> "SignedMessage":
        d = json.loads(s)
        return cls(payload=d.get("payload", {}), sig=d.get("sig", ""))


@dataclass
class ScopeSync:
    """Binds a local Scope to the swarm. Changes emit signed deltas; received
    deltas are verified and merged last-writer-wins."""
    scope: object
    key: bytes
    node_id: str = "node"
    _clock: Dict[Tuple[str, str, str], float] = field(default_factory=dict)
    rejected: int = 0

    def _sign(self, delta: ScopeDelta) -> SignedMessage:
        p = delta.payload()
        return SignedMessage(payload=p, sig=sign(p, self.key))

    # --- local changes that broadcast ------------------------------------- #

    def arm(self, value: str, kind: str, *, job: str = "", expires: Optional[float] = None,
            now: Optional[float] = None) -> SignedMessage:
        now = time.time() if now is None else now
        self.scope.add(value, kind, job=job, expires=expires, now=now)
        self._clock[(kind, value.lower())] = now
        return self._sign(ScopeDelta("add", kind, value, job, expires, now, self.node_id))

    def unarm(self, value: str, kind: str, *, job: str = "", now: Optional[float] = None) -> SignedMessage:
        now = time.time() if now is None else now
        self.scope.remove(value, job=job or None)
        self._clock[(kind, value.lower())] = now
        return self._sign(ScopeDelta("remove", kind, value, job, None, now, self.node_id))

    def emit_full(self, now: Optional[float] = None) -> List[SignedMessage]:
        """Signed deltas for the whole active scope — a periodic resync for nodes
        that missed a delta or just joined."""
        now = time.time() if now is None else now
        out = []
        for e in self.scope.active_entries(now=now):
            out.append(self._sign(ScopeDelta("add", e.kind, e.value, e.job, e.expires,
                                             self._clock.get((e.kind, e.value.lower()), now),
                                             self.node_id)))
        return out

    # --- receive ----------------------------------------------------------- #

    def apply(self, message: SignedMessage, *, now: Optional[float] = None) -> Tuple[bool, str]:
        now = time.time() if now is None else now
        if not verify(message.payload, message.sig, self.key):
            self.rejected += 1
            return False, "rejected: bad signature — not from the swarm key"
        p = message.payload
        try:
            op = p["op"]; kind = p["kind"]; value = p["value"]
            job = p.get("job", ""); ts = float(p.get("ts", 0.0))
            expires = p.get("expires")
        except (KeyError, TypeError, ValueError):
            self.rejected += 1
            return False, "rejected: malformed delta"
        ckey = (kind, value.lower())
        if ts < self._clock.get(ckey, float("-inf")):
            return False, f"ignored: stale delta (ts {ts} older than known)"
        self._clock[ckey] = ts
        if op == "add":
            self.scope.add(value, kind, job=job, expires=expires, now=now)
            return True, f"applied add {kind} {value}" + (f" [{job}]" if job else "")
        if op == "remove":
            self.scope.remove(value, job=job or None)
            return True, f"applied remove {kind} {value}" + (f" [{job}]" if job else "")
        self.rejected += 1
        return False, f"rejected: unknown op '{op}'"


# --- transport (injected; loopback for tests / no radio) --------------------- #

class LoopbackMesh:
    """In-memory mesh: register nodes, broadcast delivers a message to every OTHER
    node's apply(). Stands in for the LoRa lane with zero hardware."""
    def __init__(self):
        self._nodes: List[ScopeSync] = []
        self.delivered = 0

    def register(self, node: ScopeSync) -> None:
        self._nodes.append(node)

    def broadcast(self, sender: ScopeSync, message: SignedMessage, *, now: Optional[float] = None) -> None:
        for n in self._nodes:
            if n is sender:
                continue
            n.apply(message, now=now)
            self.delivered += 1
