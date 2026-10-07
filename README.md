# pwnagotchi-redux

A standalone field-OS platform for **Raspberry Pi 4 and Pi 5 (arm64)** — a **successor** to
pwnagotchi, not a fork. (We deliberately drop the "lesser" boards; see `docs/PLATFORM_TARGET.md`.)

> **Status: Phase-2 feature-complete in the sandbox; not yet hardware-validated.**
> The full platform — image stack, supervisor spine, radio orchestrator, detector suite,
> spatial database, packs, plugin-compat shim, CLI, and web dashboard — is built and green
> under the test suite (287 tests, no hardware required). **No part has had a real-hardware
> pass on a physical Pi yet** (see *Verification status* below). Sandbox-green ≠ done.

## What it is

pwnagotchi is a thin Python wrapper over **bettercap** (engine) + **nexmon** (monitor/injection
firmware) on a Raspberry Pi OS image. redux keeps those two open foundations and replaces
everything above them:

- **Own lean image** (pi-gen, Debian/Pi-OS stable, **arm64, Pi 4 / Pi 5 only**) — Kali's tools
  available as opt-in packs, never preinstalled.
- **bettercap driven directly** — no pwnagotchi in the path.
- **Beastcore as supervisor** (built here, modeled on the `patrickato/beastagotchi` concepts) —
  a signal bus, actions + transactions, a glass-box brain, packs, and the creature UX.
- **Radio Orchestrator** — declare an intent (online / hunt / recon / survey); it auto-assigns
  radios to roles, promotes a better adapter on hotplug, falls back on unplug. No more manual
  `airmon-ng` dance. (`redux/radio/` — real logic, tested.)
- **Glass-box brain** — shows *why* it does what it does. (Honest scope: a learning brain does
  **not** reliably beat greedy channel-hopping — proven in sim — so the brain's job is
  orchestration + legibility, not "more handshakes." See `docs/`.)

## Current state (what's built)

| Area | Module | What's there |
|---|---|---|
| Image | `image/`, `build.sh`, `redux/core/boot.py`, `docs/IMAGE_BUILD.md` | pi-gen base, overlay-rootfs, boot-budget, watchdog, UPS/battery, RAUC A/B OTA |
| Radio | `redux/radio/` | orchestrator (intent→roles), `iw phy` capability probe, udev hotplug, manager |
| Engine | `redux/engine/` | bettercap driver (REST/ws client surface) |
| Supervisor spine | `redux/core/` | `SignalBus` pub/sub, `Supervisor`, `Narrator` (creature voice), glass-box `Brain`, `ActionRegistry` + transactions, `Beastcore` capstone |
| Detection | `redux/detect/` | `DetectEngine` + registry; a suite of **passive** detectors (deauth-flood, rogue-AP, beacon-spam, surveillance-sweep, karma, WPS, BLE-tracker/flood, handshake, PMF, pineapple, PNL), confidence scoring, alert bus, replay |
| Spatial | `redux/geo/` | SQLite sighting store, WiGLE lookup + queue, position estimate, coverage, self-locate, geofence, GPX/KML export, dead-reckoning, geohash, Kismet import, stats, migrations |
| Classify | `redux/classify/` | handshake crackability scoring |
| Packs | `redux/packs/` | pack manifest + dependency-resolving manager |
| Compat | `redux/compat/` | pwnagotchi-plugin compat shim |
| Web | `redux/web/` | glass-box status dashboard (self-contained, no external assets) |
| CLI | `redux/cli.py` | `redux status / run / web / packs` |

Everything assembles on one bus through `Beastcore` and runs as a single system.

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

See `AGENTS.md` for the full contract, and `ASSIGNMENTS.md` for the enforced build lanes.

## Verification status (honest)

- **Sandbox-verified:** the whole test suite (`pytest`, 287 tests) runs green against real logic —
  orchestrator decisions, signal/brain/action spine, detectors over synthetic frames, spatial DB,
  packs, shim, CLI, web payload. Image-build scripting is unit-tested for structure/wiring.
- **Not yet verified (needs a real Pi):** booting the image on hardware; bettercap capturing a real
  handshake with zero pwnagotchi in the path; the orchestrator driving actual monitor-mode and
  hotplug on physical adapters; TFT rendering; UPS/OTA on-device. Nothing graduates until it passes
  a real-hardware pass on an actual Pi 4 / Pi 5. The step-by-step on-device checklist is
  `docs/HARDWARE_VALIDATION.md`.

## Layout

```
redux/radio/      Radio Orchestrator (decision engine + probe + hotplug)
redux/engine/     bettercap driver
redux/core/       Supervisor spine: signals, narrator, brain, actions, Beastcore
redux/detect/     passive detector suite + engine/registry
redux/geo/        spatial database + WiGLE/GPS/coverage/export
redux/classify/   handshake crackability scoring
redux/packs/      Beast Packs (manifest + manager)
redux/compat/     pwnagotchi-plugin compat shim
redux/web/        glass-box status dashboard
redux/cli.py      operator entrypoint (status / run / web / packs)
image/            pi-gen image stack (boot, overlay, watchdog, UPS, OTA)
tests/            unit/integration tests (real logic, no hardware needed)
docs/             architecture, vision, proposals
docs/reference/   material COPIED (not forked) from the Beast repos — provenance noted
```

## Quickstart (dev)

```
pip install -e ".[dev]"
pytest                 # 287 tests, no hardware needed

redux status           # glass-box status snapshot (JSON)
redux run              # run pump cycles (optionally over a recorded session)
redux web              # serve the glass-box dashboard (least-exposed bind scope by default)
redux packs            # manage Beast Packs
```

Requires Python 3.11+.

## How we build (multi-agent)

See `AGENTS.md`. Humans + a lead model set scoped tasks in `TASKS.md` / `ASSIGNMENTS.md`; each
agent works only in its **enforced branch lane** (`.github/workflows/lane-guard.yml` fails any PR
that touches another lane's paths); the **lead is the sole integrator to `main`**. Agents pull a
task, work in their lane, open a PR for review, and pull the next free task — flow, no overlaps,
no surprise merges. CI (`.github/workflows/ci.yml`) is the shared gate; nothing reaches `main` red.
