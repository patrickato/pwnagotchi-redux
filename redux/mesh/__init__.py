"""redux.mesh — off-grid swarm coordination over LoRa/Meshtastic.

Distributed Scope sync: arm a target on one node and it propagates to the swarm,
so a multi-device engagement shares one authorization set. Authenticated by
design — every delta is HMAC-signed with the swarm key and forged deltas are
rejected, extending the aiming-integrity model to the fleet. Last-writer-wins
merge converges under out-of-order LoRa delivery. The transport is injected
(LoopbackMesh for no-hardware tests; a real LoRa sender in the field).
"""
from .scope_sync import (
    ScopeDelta, SignedMessage, ScopeSync, LoopbackMesh, sign, verify,
)

__all__ = [
    "ScopeDelta", "SignedMessage", "ScopeSync", "LoopbackMesh", "sign", "verify",
]
