# pwnagotchi-redux

A standalone field-OS platform for Raspberry Pi — a **successor** to pwnagotchi, not a fork.

> **Status: pre-alpha scaffold (v0.0.1).** Architecture + the first real module (the
> Radio Orchestrator decision engine, unit-tested) are in. Nothing is flashable yet.

## What it is

pwnagotchi is a thin Python wrapper over **bettercap** (engine) + **nexmon** (monitor/injection
firmware) on a Raspberry Pi OS image. redux keeps those two open foundations and replaces
everything above them:

- **Own lean image** (pi-gen, Debian/Pi-OS stable) — Kali's tools available as opt-in packs, never preinstalled.
- **bettercap driven directly** — no pwnagotchi in the path.
- **Beastcore as supervisor** (ported from `patrickato/beastagotchi`) — signals/actions/transactions, packs, doctor, the creature UX.
- **Radio Orchestrator** — declare an intent (online / hunt / recon / survey); it auto-assigns
  radios to roles, promotes a better adapter on hotplug, falls back on unplug. No more manual
  `airmon-ng` dance. (See `redux/radio/orchestrator.py` — real logic, tested.)
- **Glass-box brain** — shows *why* it does what it does. (Honest scope: a learning brain does
  **not** reliably beat greedy channel-hopping — proven in sim — so the brain's job is
  orchestration + legibility, not "more handshakes." See `docs/`.)

## Scope

Make it **turnkey.** The whole pitch is polished, one-click, automatic wherever we can get it —
the Radio Orchestrator (you never touch monitor mode again) is the flagship. Ship turnkey modules
when possible.

What doesn't move:

- **Authorized / passive by default.** Any deauth/jam/targeting/firing-capable capability gates on
  an explicit authorized-target allowlist (BSSID/SSID), **empty by default** — never on
  physical/signal-range assumptions.
- The one kind of module we don't ship turnkey is a **turnkey attack/weaponized module**
  (one-click-to-fire). Everything legitimate, make as one-click as you can.
- **Glass-box + real data only** — every decision carries a human-readable reason; no decorative
  fake telemetry.

See `AGENTS.md` for the full contract every agent (and human) follows here.

## Layout

```
redux/radio/      Radio Orchestrator (decision engine + live wiring)
redux/engine/     bettercap driver
redux/core/       Supervisor (Beastcore's promoted role)
tests/            unit tests (run against real logic, no hardware needed)
docs/             architecture, vision, proposals
docs/reference/   material COPIED (not forked) from the Beast repos — provenance noted
```

## Quickstart (dev)

```
pip install -e ".[dev]"
pytest
```

## How we build (multi-agent)

See `AGENTS.md`. Humans + a lead model set scoped tasks in `TASKS.md`; agents execute in their
own branch space; CI (`.github/workflows/ci.yml`) is the shared gate; the lead reviews and
merges. Nothing reaches `main` red.
