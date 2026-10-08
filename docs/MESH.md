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

## Swarm-key lifecycle (`redux/mesh/keyring.py`)

The shared HMAC key is the swarm's trust anchor, so it has a life story instead of
being a hardcoded constant:

- **Generate** a random 32-byte key when you arm a lab/job (`generate_key`).
- **Rotate** per job (`SwarmKeyring.rotate`): a new key becomes current, and a
  bounded tail of recent keys is kept so deltas signed just before the rotation
  still verify — that grace window is `ScopeSync.verify_keys` (signing always uses
  the current key; verifying accepts the ring). `scopesync_keys(ring)` returns the
  `(signing_key, [grace_keys])` pair to wire in.
- **Expiry** per key: a lapsed key drops out of `active()`/`verifiers()`, so expired
  authorization stops authorizing — same rule the Scope uses.
- **Exchange** out-of-band: `export_token` → a compact `AKEY1:` token to show as a
  QR or send over the LoRa lane to a trusted peer; `import_token` on the other side.
  The token carries key material by design — share it over a channel you trust.
- **Sealed at rest**: `save`/`load` go through `redux.vault`, so the keystore is
  encrypted and the persistence path is honest/fail-closed (no crypto backend → it
  refuses rather than writing the key in plaintext).

CLI: `redux mesh key gen|rotate|show|export|import` (passphrase via `AUGUR_PASSPHRASE`
or prompt; `show` prints key ids + status, never the key material).

## Needs-hardware

The real transport — a Meshtastic/LoRa sender/receiver wired to `broadcast` and
`apply` — plus LoRa duty-cycle/bandwidth pacing (deltas are tiny by design, with
periodic `emit_full` resync). The signing, merge, and rejection logic are all
sandbox-verified; only the radio link is a Pi step. Auto-loading the active key
from a sealed keystore into the running `ScopeSync` at boot is the remaining wire-up
(pairs with the config.toml item).
