"""Coordinated multi-device capture chaining over the swarm.

Scope sync (``scope_sync.py``) federates *authorization* — arm a target once and
every node agrees it is in scope. This module federates *execution*: split the
recon → capture → crack → pivot chain across the devices in the swarm so a
multi-radio engagement runs in parallel instead of one box doing everything.

The one invariant, and the reason this is safe to coordinate at all:

    **Authorization is never delegated.** The coordinator distributes *work*,
    not permission. Every worker runs its own local ``Operator`` — posture →
    Scope → capability — against the assigned target before it fires. A worker
    that is handed a target it cannot independently authorize against its *own*
    synced Scope refuses it. So a spoofed or compromised coordinator cannot make
    a node fire at something the node did not already agree to; the worst it can
    do is waste a node's time, never aim it.

That is why assignments ride the *same* signed transport as scope deltas: a
``TaskDelta`` is HMAC-signed with the swarm key and verified on receipt, so an
unsigned assignment injected by any radio in range is rejected outright — and
even a correctly-signed one still clears the receiving node's local gates.

Capability-aware placement: the coordinator assigns a phase to a node only if
that node advertises the capability the phase needs (a capture-phase target goes
to a node whose ``capture.handshake`` is present; a node with no monitor-mode
radio draws recon-only). Placement is glass-box — every assignment and every
skip carries a human-readable reason.

Pure/testable: the transport is injected (the ``LoopbackMesh`` from
``scope_sync`` works unchanged), and workers take an injected ``Operator`` and
injected executors, so the whole fan-out is exercised with no radio.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..operator.operator import Operator, Phase, CHAIN, _PHASE_META
from ..core.scope import classify_target
from .scope_sync import SignedMessage, sign, verify


# Capabilities a node must advertise for a phase to be placed on it. Phases with
# no hard capability (crack/pivot/loot run on compute, not a radio) map to None
# and can be placed on any node that cleared the earlier gated phases.
_PHASE_CAPABILITY: Dict[Phase, Optional[str]] = {
    phase: _PHASE_META[phase][1] for phase in CHAIN
}


@dataclass(frozen=True)
class NodeProfile:
    """What one swarm node can do, as the coordinator sees it. ``caps`` is the
    same capability-id → present map an ``Operator`` takes."""
    node_id: str
    caps: Dict[str, bool] = field(default_factory=dict)
    offense_enabled: bool = True

    def can(self, capability: Optional[str]) -> bool:
        return capability is None or bool(self.caps.get(capability, False))


@dataclass(frozen=True)
class TaskDelta:
    """A signed work assignment: run ``phase`` against ``target`` (job ``job``) on
    node ``assignee``. The analogue of ScopeDelta for execution, so it rides the
    same authenticated lane."""
    assignee: str
    target: str
    phase: str              # Phase value
    job: str = ""
    ts: float = 0.0
    origin: str = ""        # coordinating node id

    def payload(self) -> dict:
        return {"assignee": self.assignee, "target": self.target, "phase": self.phase,
                "job": self.job, "ts": self.ts, "origin": self.origin}


@dataclass(frozen=True)
class Assignment:
    """One placement decision (glass-box). ``assignee`` is None when no eligible
    node could take the work — the reason says why."""
    target: str
    phase: Phase
    assignee: Optional[str]
    reason: str

    def to_dict(self) -> dict:
        d = {"target": self.target, "phase": self.phase.value,
             "assignee": self.assignee, "reason": self.reason}
        return d


@dataclass
class SwarmCoordinator:
    """Places the capture chain across known nodes and emits signed task deltas.

    It decides WHERE work runs; it never decides whether a target is authorized —
    that is each worker's local Scope, re-checked at execution time."""
    key: bytes
    node_id: str = "coordinator"
    nodes: List[NodeProfile] = field(default_factory=list)

    def add_node(self, profile: NodeProfile) -> None:
        self.nodes = [n for n in self.nodes if n.node_id != profile.node_id]
        self.nodes.append(profile)

    def _sign(self, delta: TaskDelta) -> SignedMessage:
        p = delta.payload()
        return SignedMessage(payload=p, sig=sign(p, self.key))

    def _eligible(self, phase: Phase) -> List[NodeProfile]:
        cap = _PHASE_CAPABILITY[phase]
        return [n for n in self.nodes if n.offense_enabled and n.can(cap)]

    def plan(self, targets: Sequence[str], *, job: str = "") -> List[Assignment]:
        """Glass-box placement for each target's chain across the swarm — nothing
        is signed or sent. A phase whose capability no node advertises is left
        unassigned with a reason (the chain is contingent on the earlier phase
        anyway, exactly as the single-node Operator plan is)."""
        out: List[Assignment] = []
        if not self.nodes:
            for t in targets:
                out.append(Assignment(t, Phase.CAPTURE, None,
                                      "no nodes registered in the swarm"))
            return out
        # Round-robin within the eligible set per phase, so parallel targets spread
        # across devices instead of piling onto the first capable node.
        cursor: Dict[Phase, int] = {phase: 0 for phase in CHAIN}
        for t in targets:
            for phase in CHAIN:
                eligible = self._eligible(phase)
                cap = _PHASE_CAPABILITY[phase]
                if not eligible:
                    detail = (f"no node advertises '{cap}'" if cap
                              else "no offense-enabled node available")
                    out.append(Assignment(t, phase, None, f"unplaced: {detail}"))
                    continue
                pick = eligible[cursor[phase] % len(eligible)]
                cursor[phase] += 1
                captag = f" (needs '{cap}')" if cap else ""
                out.append(Assignment(t, phase, pick.node_id,
                                      f"placed on {pick.node_id}{captag}"))
        return out

    def assignments(self, targets: Sequence[str], *, job: str = "",
                    now: Optional[float] = None) -> List[SignedMessage]:
        """Signed TaskDeltas for every placed phase. Unplaced phases produce no
        message (there is nothing to dispatch). Workers still re-gate locally."""
        now = time.time() if now is None else now
        out: List[SignedMessage] = []
        for a in self.plan(targets, job=job):
            if a.assignee is None:
                continue
            out.append(self._sign(TaskDelta(a.assignee, a.target, a.phase.value,
                                            job, now, self.node_id)))
        return out


@dataclass
class SwarmWorker:
    """A node that receives signed task deltas and runs the gated chain locally.

    It verifies the signature (forged assignments rejected), confirms the delta is
    addressed to it, then hands the target to its *own* ``Operator`` — which
    re-applies posture → Scope → capability. The assignment is a suggestion of
    *what to work on*; the local gates decide whether it may fire. Executors are
    injected per phase exactly as ``Operator.run`` expects."""
    operator: Operator
    key: bytes
    node_id: str = "node"
    executors: Dict[Phase, Callable[[str], Tuple[bool, str]]] = field(default_factory=dict)
    verify_keys: List[bytes] = field(default_factory=list)
    rejected: int = 0
    bus: object = None

    def _emit(self, signal: str, **payload) -> None:
        if self.bus is not None:
            try:
                self.bus.emit(signal, **payload)
            except Exception:
                pass   # telemetry must never break execution

    def accept(self, message: SignedMessage, *, now: Optional[float] = None) -> dict:
        """Verify, address-check, locally gate, then (if the gate passes and an
        executor exists) run the one phase. Returns a glass-box result dict; it
        NEVER fires on a target the local Operator does not authorize."""
        if not (verify(message.payload, message.sig, self.key)
                or any(verify(message.payload, message.sig, k) for k in self.verify_keys)):
            self.rejected += 1
            return {"ok": False, "fired": False,
                    "reason": "rejected: bad signature — not from the swarm key"}
        p = message.payload
        try:
            assignee = p["assignee"]; target = p["target"]; phase = Phase(p["phase"])
            job = p.get("job", "")
        except (KeyError, TypeError, ValueError):
            self.rejected += 1
            return {"ok": False, "fired": False, "reason": "rejected: malformed task delta"}
        if assignee != self.node_id:
            return {"ok": False, "fired": False,
                    "reason": f"ignored: addressed to '{assignee}', not me"}
        # THE invariant: re-gate locally. The assignment never substitutes for the
        # node's own posture/Scope/capability check.
        allowed, authorized, reason = self.operator._gate(target, phase)
        if not allowed:
            self._emit("ACTION", action="swarm_task_refused", target=target, reason=reason)
            return {"ok": False, "fired": False, "authorized": authorized,
                    "target": target, "phase": phase.value,
                    "reason": f"local gate refused: {reason}"}
        ex = self.executors.get(phase)
        if ex is None:
            return {"ok": False, "fired": False, "authorized": True,
                    "target": target, "phase": phase.value,
                    "reason": f"authorized, but no executor for {phase.value} on this node"}
        self._emit("TX_BEGIN", action=_PHASE_META[phase][0], target=target, reason=reason)
        ok, res = ex(target)
        self._emit("TX_END", action=_PHASE_META[phase][0], target=target, result=res)
        return {"ok": ok, "fired": True, "authorized": True, "target": target,
                "phase": phase.value, "result": res, "reason": reason}
