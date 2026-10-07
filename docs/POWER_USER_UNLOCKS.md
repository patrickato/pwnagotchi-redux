# Power-user unlocks — the friction switches

This is a **knowledge map**, not a key handover. It catalogues the switches that
remove *friction* so the full, in-scope capability is actually reachable — the
stuff a power user should know exists and where it lives. Every item here is on
one side of a line we hold deliberately:

## The line (read this first)

There is a fine but **distinct** difference, and we never lump the two together:

- **Unfiltering** = removing friction so legitimate, authorized, full-scope use is
  easy. Auto-arming *your own* kit, bulk-importing *your* engagement's targets,
  flipping the box into the right posture in one gesture. **Everything in this doc
  is this.** We push this as hard as we can.
- **Unaiming** = removing the requirement that a target be yours or authorized.
  That is the bystander class (indiscriminate spam, jamming). **Nothing here does
  that, and nothing ever will.** Scope still decides WHERE; these switches only
  make aiming *at what's already yours* frictionless.

Rule of thumb: an unlock here changes *how easily* you arm/configure, never *what
you're allowed to point at*.

## Scope — getting armed fast

| Switch | Where | What it removes |
|---|---|---|
| `redux scope arm-lab --auto` | `redux/core/labscope.py` | Zero-typing: detects your *own* private subnet, radios, and broadcast AP and arms them. A public uplink is shown but never auto-armed. |
| `redux scope arm-lab --bssid/--ssid/--cidr` | `scope.py` | One gesture to pre-authorize your own gear, no expiry. |
| `redux scope import <file> [--job]` | `scope.py` `bulk_load()` | Paste a whole engagement's target list (one per line, `#` comments ok) → armed in bulk. |
| `redux scope add <t> --job --expires-days` | `scope.py` | Per-engagement groups + auto-expiry so lapsed permission stops authorizing itself. |
| `REDUX_SCOPE` env | `cli.py` | Point at a per-job scope file; keep several engagements side by side. |

## Personas — one box, pick your hat

| Switch | Where | What it removes |
|---|---|---|
| `redux persona apply <red/blue/purple/recon/mesh/sigint>` | `redux/core/persona.py` | One gesture sets intent + detector set + exposure + posture for a whole job. |
| posture | `persona.py` | *Extra* gate only: `blue`/`recon` hard-disable firing regardless of Scope. Posture can only **tighten**, never widen — so a persona is a safe default, not an unlock of aiming. |

## Capture / engine

| Switch | Where | What it removes |
|---|---|---|
| alternate capture engine (AngryOxide) | capability graph (`CAPTURE_HANDSHAKE` provider) | Surgical, validated-crackable capture when you want the scalpel; falls back to bettercap when absent. See `docs/ENGINE_BAKEOFF.md`. |
| `--notransmit` / passive postures | engine + persona | Full recon with zero frames emitted — the opposite unlock, for when you want quiet. |

## Sharing / safety without losing capability

| Switch | Where | What it removes |
|---|---|---|
| `redux ghost in out` | `redux/replay/ghost.py` | Share or demo a real session with identities pseudonymized + location dropped — so "I can't show anyone my captures" stops being a blocker. |
| `redux run/web --replay` | `cli.py` | Develop and demo the whole stack with no radio/hardware. |
| `--bind-scope localhost/lan/tailscale/auto` | `redux/web/status_page.py` | Choose exposure per deployment; least-exposed by default, wide-open when *you* decide. |

## Seeing the truth (so you trust the unlocks)

| Switch | Where | What it gives |
|---|---|---|
| `redux doctor` | `redux/core/doctor.py` | Headless glass-box self-diagnosis; UNKNOWN where it couldn't check (never a fake green). |
| `redux post [--tft]` | `redux/core/post.py` | Boot self-test that can't show READY unless the critical probe actually passed. |
| `redux scope list` | `cli.py` | Exactly what's armed, per job, with expiry — so you always know what *can* fire. |

## Adding a new unlock? Keep it on the right side of the line.

A new convenience belongs here if it makes authorized, in-scope use easier. If it
would let the box fire at something the operator hasn't put in Scope, it's not an
unlock — it's unaiming, and it doesn't ship. When in doubt: does it change *how
easily you arm*, or *what you're allowed to hit*? Only the first.
