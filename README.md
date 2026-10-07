# pwnagotchi-redux

A standalone field-OS platform for **Raspberry Pi 4 and Pi 5 (arm64)** — a **successor** to
pwnagotchi, not a fork. (We deliberately drop the "lesser" boards; see `docs/PLATFORM_TARGET.md`.)

> **Status: feature-complete in the sandbox; not yet hardware-validated.**
> The full platform — image stack, supervisor spine (signals/brain/actions), capability graph,
> central Scope, resource Governor, Doctor, boot-POST, personas, radio orchestrator, bettercap
> driver, 13-detector suite, CSI motion/occupancy sensing, spatial DB + Field Dex, scope-gated
> offense (capture→crack, network kill-chain, captive portal), SDR passive-sensing, Expeditions,
> record/ghost, packs, plugin-compat shim, CLI, and web dashboard —
> is built and green under the test suite (500+ tests, no hardware required). **No part has had a
> real-hardware pass on a physical Pi yet** (see *Verification status*). Sandbox-green ≠ done.

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
| Capability graph | `redux/core/capabilities.py` | shared capability vocabulary; packs/radios/GPS/SDR/firing gate as providers; `explain()` / `active_provider()` / `blast_radius()` |
| Central Scope | `redux/core/scope.py`, `redux/core/labscope.py` | the one authorized-target list every firing function consults (BSSID/SSID/CIDR, per-job, expiry, bulk-load, arm-lab, **auto-arm your own kit**) |
| Governor | `redux/core/governor.py` | heat/power/load/RAM shedding (FULL→SURVIVAL), immediate-escalate + held recovery; drives write/loop cadence |
| Doctor | `redux/core/doctor.py` | headless glass-box self-diagnosis over the graph (OK/ATTENTION/DEGRADED/ACTION + coverage honesty) |
| Boot-POST | `redux/core/post.py` | power-on self-test: a streaming boot checklist that can't show READY unless the critical probe actually passed (the Doctor, gated) |
| Detection | `redux/detect/` | `DetectEngine` + registry; 13 **passive** detectors (deauth-flood, rogue-AP, beacon-spam, surveillance-sweep, karma, WPS, BLE-tracker/flood, handshake, PMF, pineapple, PNL, hidden-SSID), confidence, alert bus, replay |
| Spatial | `redux/geo/` | SQLite sighting store (WAL, coalesced writes), WiGLE, coverage, self-locate, geofence, GPX/KML export, dead-reckoning, geohash, Kismet import, stats |
| Field Dex | `redux/dex/` | recon ledger over sightings — first/last-seen, rarity, "departed", **+ device identity that survives MAC randomization** (PNL/IE fingerprint linking; OUI never links) |
| CSI sensing | `redux/sense/` | the radio as a motion/presence sensor (nexmon_csi): motion + occupancy, UNKNOWN until calibrated — sandbox math done, wire-parser needs-hardware |
| Classify | `redux/classify/` | handshake crackability scoring |
| Offense (scope-gated) | `redux/crack/`, `redux/netrecon/`, `redux/portal/` | dual-engine capture (bettercap + **AngryOxide** scalpel behind the capability graph), capture→crack pipeline, network kill-chain (scan/enumerate/cred-test/loot), captive portal for authorized testing |
| SDR | `redux/sdr/` | passive-sensing ingest (rtl_433 ISM + ADS-B) into the same sightings store/Dex |
| Frameworks / Range | `redux/frameworks/` | ATT&CK/D3FEND/PTES map for every action + **purple Range mode** (attack yourself, grade your own detectors, name the gaps) |
| Engagement report | `redux/report/` | chain-of-authorization deliverable — every action re-checked against Scope, **out-of-scope actions flagged not hidden**, ATT&CK-tagged, optional ghost-sanitized |
| Expeditions | `redux/expedition/` | named field sessions + "Wrapped" recap |
| Replay / Ghost | `redux/replay/` | record a session; produce a **sanitized ghost** (identities pseudonymized, location dropped, timing preserved) to replay or share without leaking real recon data |
| Packs / Compat | `redux/packs/`, `redux/compat/` | pack manifest + dependency resolver; pwnagotchi-plugin compat shim |
| Web | `redux/web/` | glass-box status dashboard + sightings map (self-contained, no external assets) |
| Personas | `redux/core/persona.py` | one box, pick your hat (red/blue/purple/recon/mesh/sigint) — a declarative capability+posture set; posture is an *extra* gate that can only tighten firing, never widen it |
| CLI | `redux/cli.py` | `redux status / run / web / packs / doctor / post / persona / sense / capture / range / report / scope / dex / expedition / ghost` |

Everything assembles on one bus through `Beastcore` and runs as a single system. Offensive
capabilities are full-power but consult the central Scope for aiming (see below).

## Scope — a full toolkit, not a cage

Make it **turnkey.** The whole pitch is polished, one-click, automatic wherever we can get it —
the Radio Orchestrator (you never touch monitor mode again) is the flagship.

redux ships the **full offensive toolkit at full power** — capture→crack, PMKID/assoc, deauth, the
network-layer kill-chain, evil-portal — none of it watered down. The one thing that's centralized
is *aiming*:

- **One central Scope** (`redux/core/scope.py`). Every firing-capable function consults the same
  authorized-target list — the targets you **own or are authorized to assess**: your own networks
  and devices, your whole lab, gear you bought to test, engagements you're contracted for, ranges,
  CTFs, and consenting peers. Broad by design.
- **Arming is one gesture.** It starts empty only so the device never fires at something you didn't
  choose. `redux scope add` / `import` (bulk-load a pasted list) / `arm-lab` (pre-authorize your own
  kit) / `arm-lab --auto` (detect your own private subnet, radios, and broadcast AP and arm them with
  zero typing — a public uplink is shown but never auto-armed) — with per-job groups and optional
  expiry so lapsed permission stops authorizing itself.
- **Scope decides WHERE it's aimed, never WHAT it can do.** Inside scope, nothing is held back and
  it's as one-click as we can make it.
- **Detection-only exceptions** (the whole list): functions that can't be aimed and hit bystanders —
  indiscriminate BLE/beacon spam and RF jamming (also illegal to transmit). We detect them, never
  emit them.
- **Glass-box + real data only** — every decision carries a human-readable reason; no decorative
  fake telemetry.

See `AGENTS.md` for the full contract, and `ASSIGNMENTS.md` for the enforced build lanes.

## Verification status (honest)

- **Sandbox-verified:** the whole test suite (`pytest`, 511 tests) runs green against real logic —
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
redux/core/       Supervisor spine: signals, narrator, brain, actions, capability graph,
                  Scope, Governor, Doctor, boot-POST, Beastcore
redux/detect/     passive detector suite + engine/registry
redux/geo/        spatial database + WiGLE/GPS/coverage/export
redux/dex/        Field Dex (recon ledger) + device fingerprint identity (cross-MAC re-id)
redux/sense/      CSI sensing — the radio as a motion/presence sensor (nexmon_csi)
redux/classify/   handshake crackability scoring
redux/crack/      capture→crack pipeline + dual-engine capture providers (bettercap + AngryOxide)
redux/netrecon/   network kill-chain: scan/enumerate/cred-test/loot (scope-gated)
redux/portal/     captive portal for authorized client testing
redux/sdr/        passive SDR ingest (rtl_433 ISM + ADS-B)
redux/frameworks/ ATT&CK/D3FEND/PTES map + purple Range mode (grade your detectors)
redux/report/     engagement report — chain of authorization, out-of-scope flagged
redux/expedition/ named field sessions + Wrapped recap
redux/replay/     record a session + sanitized "ghost" for safe replay/sharing
redux/packs/      Beast Packs (manifest + manager)
redux/compat/     pwnagotchi-plugin compat shim
redux/web/        glass-box status dashboard + sightings map
redux/cli.py      operator entrypoint (status/run/web/packs/doctor/post/persona/sense/capture/range/report/scope/dex/expedition/ghost)
image/            pi-gen image stack (boot, overlay, watchdog, UPS, OTA)
tests/            unit/integration tests (real logic, no hardware needed)
docs/             architecture, vision, proposals
docs/reference/   material COPIED (not forked) from the Beast repos — provenance noted
```

## Quickstart (dev)

```
pip install -e ".[dev]"
pytest                 # 511 tests, no hardware needed

redux status           # glass-box status snapshot (JSON)
redux run              # run pump cycles (optionally over a recorded session)
redux web              # serve the glass-box dashboard (least-exposed bind scope by default)
redux doctor           # headless self-diagnosis (OK / ATTENTION / DEGRADED / ACTION)
redux post             # boot-POST (power-on self-test); exit 0/1/2 = READY/DEGRADED/HALT
redux persona list     # one box, pick your hat (apply red/blue/purple/recon/mesh/sigint)
redux sense demo       # CSI motion/occupancy pipeline on synthetic data (no radio)
redux scope            # manage the central authorized-target list (add/import/arm-lab)
redux dex              # the Field Dex — recon ledger over sightings
redux dex --identities # devices re-identified across MAC randomization (PNL/IE)
redux capture plan     # which capture engine is selected + the exact Scope-aimed command
redux range demo       # purple: attack set graded vs your detectors (ATT&CK → D3FEND)
redux report demo      # chain-of-authorization engagement report (out-of-scope flagged)
redux expedition       # start/end a field session + Wrapped recap
redux ghost in.json out.json   # sanitize a recording for safe replay/sharing
redux packs            # manage Beast Packs
```

Requires Python 3.11+.

## How we build (multi-agent)

See `AGENTS.md`. Humans + a lead model set scoped tasks in `TASKS.md` / `ASSIGNMENTS.md`; each
agent works only in its **enforced branch lane** (`.github/workflows/lane-guard.yml` fails any PR
that touches another lane's paths); the **lead is the sole integrator to `main`**. Agents pull a
task, work in their lane, open a PR for review, and pull the next free task — flow, no overlaps,
no surprise merges. CI (`.github/workflows/ci.yml`) is the shared gate; nothing reaches `main` red.
