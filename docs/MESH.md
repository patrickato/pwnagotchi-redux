# Distributed Scope sync over the mesh (`redux/mesh/`)

## Why

The multi-device dream: arm a target on one node and it propagates to every node
over the LoRa/Meshtastic lane, so a coordinated engagement shares **one**
authorization set — no re-arming each box, and lapsed permission stops authorizing
across the whole fleet at once.

## The headline is authentication, not propagation

A distributed arm that any radio in range could inject into would be *worse* than
no sync — it would let a stranger arm your fleet at a target you never chose. So:

- **Every delta is HMAC-SHA256 signed with the swarm's shared key.** `apply()`
  verifies the signature first and **rejects** anything that fails — glass-box
  reason, Scope untouched, rejection counted.
- A tampered payload (re-signed value) fails the same check.
- This is the aiming-integrity model extended to the swarm: only the swarm can
  decide where the swarm is aimed.

## Convergence

Deltas carry a timestamp and merge **last-writer-wins** per `(kind, value, job)`,
so out-of-order LoRa delivery converges and a **stale** delta can't resurrect a
removed target or delete a newer one. Expiry rides along, so a lapsed target drops
everywhere. `emit_full()` resyncs a node that joined late or missed a frame.

## What's built (sandbox-verified)

- `ScopeDelta` / `SignedMessage` (compact, fits a LoRa frame), `sign`/`verify`.
- `ScopeSync` — binds a local Scope to the swarm: `arm()`/`unarm()` apply locally
  and return a signed delta to broadcast; `apply()` verifies + merges; `emit_full()`
  resyncs.
- `LoopbackMesh` — in-memory transport for no-hardware tests (proves convergence
  and forgery rejection across nodes).
- CLI: `redux mesh demo` (3-node swarm: an arm converges everywhere; a forged
  delta is rejected).

## Needs-hardware

The real transport — a Meshtastic/LoRa sender/receiver wired to `broadcast` and
`apply` — plus LoRa duty-cycle/bandwidth pacing (deltas are tiny by design, with
periodic `emit_full` resync). The signing, merge, and rejection logic are all
sandbox-verified; only the radio link is a Pi step.
