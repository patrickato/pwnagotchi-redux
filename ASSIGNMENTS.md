# ASSIGNMENTS — lanes (enforced by CI)

Three agents build this repo in parallel. **Lanes are enforced** by
`.github/workflows/lane-guard.yml`: a PR that edits a file outside its branch's lane **fails CI**
and cannot be merged green. This is not a polite request — it is a gate. One topic per branch;
open a PR; the owner merges.

_Gemini has been removed from this project. Do not re-add a `gemini/` lane or any Gemini CI agent._

## Lanes (exclusive — the guard enforces these exact paths)

| Agent | Branch | May edit ONLY | Everything else |
|---|---|---|---|
| **Codex** (GPT) | `codex/<topic>` | `image/**`, `pi-gen/**`, `boot/**`, `build.sh`, `docs/IMAGE_BUILD.md`, `redux/core/boot.py`, `tests/test_{image_build,nexmon_build,boot}.py` | blocked by CI |
| **Grok** (xAI) | `grok/<topic>` | `redux/detect/**`, `tests/test_detect_*.py`, `redux/geo/**`, `tests/test_geo_*.py` | blocked by CI |
| **Claude** (lead) | `claude/<topic>` | everything (owns `redux/radio`, `redux/engine`, `redux/core` except `boot.py`, `redux/classify`, `etc/`, `scripts/`, all docs, all shared files, `.github/`) | — |

**Shared / structural files are LEAD-ONLY** and the guard blocks every non-lead branch from them:
`.github/**`, `README.md`, `AGENTS.md`, `ASSIGNMENTS.md`, `TASKS.md`, `docs/*` (except Codex's
`docs/IMAGE_BUILD.md`), every `__init__.py`, `pyproject.toml`, `.github/workflows/ci.yml`. If your
task seems to need a shared-file change, it doesn't — hand that to the lead (`claude/*`), who
consolidates docs/exports/CI. This is the rule that keeps overlaps, clobbered merges, and stray
workflows from ever reaching `main`.

## Current assignments

### Status — 2026-10-07
- **Grok (xAI) is offline until ~2026-10-08** (hit a ~13-hour usage limit around 07:28 ET). For
  the day the **lead absorbs the `redux/detect/**` + `redux/geo/**` lane**: the lead may land
  detect/geo work on `claude/*` branches (the guard grants lead full access). Lead changes inside
  Grok's lane are marked in their commit messages as "(Grok absorbed, out for the day)". When Grok
  returns it **pulls latest `main` first** and resumes from there — no stale-base branches.
- **Codex (GPT)** active and streaming in the image lane.

### Codex → `codex/*` — the image/OS build
Lean **arm64** pi-gen image for **Pi 4 / Pi 5**, bettercap + nexmon baked, `redux.service` →
`python -m redux.core.boot`. Read-only overlay rootfs (yank-safe), sub-15s boot, hardware watchdog.
Acceptance: `./build.sh` produces an arm64 image; boot/overlay/watchdog documented; on-Pi flash is
the labeled hardware gate. **Do not** touch README/TASKS/AGENTS — the lead folds any needed
doc/task updates; put image docs in `docs/IMAGE_BUILD.md` only.

### Grok → `grok/*` — the defensive detector pack (`redux/detect/`)
deauth-flood, rogue-AP/evil-twin, beacon-spam, surveillance-sweep detectors + the `DetectEngine`
fan-in, emitting glass-box alerts. Standalone `Frame` input — do **not** import `redux.engine`
(the lead wires it to the real `Event` at integration). Pure defense: raises alerts, transmits
nothing. Acceptance: hardware-free tests under `tests/test_detect_*.py`. **Stay entirely in
`redux/detect/`** — nothing else, ever.

### Claude (lead) → `claude/*` — core + integration + review
Owns `redux/radio` (orchestrator/manager/probe/hotplug), `redux/engine` (bettercap driver),
`redux/core` (supervisor), `redux/classify`, `etc/` + `scripts/` (udev/hotplug), all docs, all
shared files, `.github/`. Sets tasks + acceptance, reviews every PR against the guard + its
acceptance, reconciles exports/docs, keeps the safety gates. **Merges are the owner's.**

## Scope (non-negotiable, all lanes)
Authorized/passive by default; any firing-capable capability gates on an empty-by-default
BSSID/SSID allowlist; **no turnkey-attack modules**; Pi 4/Pi 5 arm64 only; glass-box; real data only.

## Lane backlogs (workhorse queue — pull the top item not yet in a PR)

Each agent works a loop: take the next open item in **your** backlog, branch `your-prefix/<topic>`,
build it with hardware-free tests, open a PR, take the next. One item per branch. Never merge; the
lead integrates. Mark an item `[PR #n]` when you open its PR so the next agent run skips it.

**Codex backlog** (image/OS lane — `image/ pi-gen/ boot/ build.sh docs/IMAGE_BUILD.md redux/core/boot.py`):
1. `[PR #1]` arm64 pi-gen image boots to `redux.service` on Pi 4/5 (bettercap + nexmon baked)
2. Read-only overlay rootfs (yank-safe); only a captures partition writable
3. Sub-15s boot — prune systemd units, trim initramfs; put `systemd-analyze blame` in the PR
4. Hardware watchdog (BCM2835 + `RuntimeWatchdogSec`) + crash-safe resume
5. UPS HAT read + graceful low-battery shutdown + battery state on the TFT
6. A/B OTA image updates (RAUC) with automatic rollback on failed boot

**Grok detector backlog** (`redux/detect/ tests/test_detect_*.py`) — items 1–6 all DONE/merged:
1–6. `[done]` detector pack, surveillance, Karma, WPS, alert bus/config, README+NOTES.

**Grok NEW backlog — BeastSpatialDB (geo lane, `redux/geo/ tests/test_geo_*.py`)** — Phase 2.2, the
spatial keystone. Pull in order; new module, all hardware-free tests. (This is the lane Gemini left.)
1. `redux/geo/db.py` — a unified **sighting store** (SQLite, stdlib `sqlite3`): one schema for every
   sighting (WiFi/BLE/SDR) — `bssid/mac, ssid, kind, lat, lon, rssi, channel, source_radio, ts,
   provenance`. Insert + query API; dedup by key keeping best-RSSI/first-seen. Glass-box provenance
   on every row. Tests: insert/query/dedup round-trip.
2. `redux/geo/wigle.py` — export the store to **WiGLE `WigleWifi-1.6` CSV** (correct header + field
   order) and a `kismetdb_to_wiglecsv`-compatible dump. Tests: round-trip a few rows → valid CSV.
3. `redux/geo/estimate.py` — **RSSI weighted-centroid** AP location from accumulated sightings, with
   an observation count + a rough confidence/error radius. Pure math; tests on synthetic sightings.
4. `redux/geo/coverage.py` — survey **coverage/gap grid** (geohash or simple lat/lon buckets): which
   cells are covered vs unvisited, to guide driving. Tests on synthetic tracks.
5. `redux/geo/README.md` + `NOTES.md` — schema, the bus default path
   (`/etc/pwnagotchi/…` per CONVENTIONS), query examples, what's sandbox-tested vs needs real GPS.

Then, back in the detector lane if you want more: a **BLE tracker/AirTag detector** and a
**PMKID/handshake-capture detector** — both `redux/detect/`, same rules.

Rule reminder: do NOT import `redux.engine` or touch any other lane; the lead wires geo to the live
GPS/event sources at integration. Everything stays under `redux/detect/` **or** `redux/geo/`.


## Next-batch backlogs (both lanes cleared their first queues — pull from here next)

**Codex next** (image/infra lane): 
7. Beast Packs infra — an apt repo layout + a `packs` install/remove helper (opt-in Kali-tools pack), image-side only.
8. A `flash.sh` / release helper that writes the built image to an SD card and verifies it.
9. CI for the image build (lint the stage scripts; dry-run `./build.sh` arg parsing).

**Grok next** (detector + geo lanes):
7. BLE tracker / AirTag-style unwanted-tracker detector (`redux/detect/`).
8. PMKID / handshake-capture detector (`redux/detect/`).
9. `redux/geo/` WiGLE dedup + incremental upload queue (dedup by BSSID, best-RSSI/first-seen).
10. `redux/geo/` offline self-geolocation: estimate own position from visible known APs in the store.

## If the guard fails your PR
It printed exactly which file is out of lane and which paths your lane allows. Remove the stray file
from your branch (it belongs to another lane), or ask the lead to make the shared-file change. Do
not widen your lane to make it pass.

## Grok DEEP backlog (work all of these before pinging for more — detect lane `redux/detect/`, geo lane `redux/geo/`)

Same loop, one item per branch `grok/<topic>`, hardware-free tests, PR to main, never merge.
Stay strictly in `redux/detect/` OR `redux/geo/`. If a detector adds an `AlertKind`/`FrameType`,
that's fine (the lead unions them at merge). Keep going down this list in order:

Detect:
- D1. Probe-request PNL harvester + detector: build a "device → preferred-network-list" view from
  probe requests; flag a device loudly probing for many/known SSIDs. Passive recon.
- D2. Pineapple / PineAP / MANA detector: one radio answering probes for many distinct SSIDs
  (karma-at-scale) / beacon-flooding signatures.
- D3. PMF-missing advisory: flag WPA2 APs advertising no 802.11w (deauth-vulnerable) as a posture
  warning (info severity).
- D4. BLE advertisement-flood / BLE-spam detector (Flipper-style spam bursts).
- D5. Detector registry + unified config: a registry listing every detector with its
  `DEFAULTS`/`_opt` config so `DetectEngine` can auto-include new ones (reduces wiring).
- D6. Per-alert confidence score (0..1) + rationale, added to `Alert` detail.
- D7. Frame replay harness: load recorded frames from JSON and run the engine (debug/test aid).

Geo:
- G1. Return-to-signal: given a BSSID, bearing + distance from a current position to its
  peak-RSSI sighting in the store.
- G2. Geofence module: point-in-polygon against a GeoJSON polygon ("am I inside the authorized area").
- G3. GPX + KML track/sighting export from the store.
- G4. Dead-reckoning gap-fill: interpolate position across GPS dropouts from speed/heading.
- G5. Spatial index helper (geohash or H3-style buckets) for fast cell queries.
- G6. kismetdb import: read a Kismet SQLite into the sighting store.
- G7. Store stats/query API: top SSIDs, new-since-<ts>, densest cells, counts by security.
- G8. Sighting-store schema versioning + migrations.

## Grok TOP PRIORITY (do this FIRST, before anything else)

- **DW1. Wire every detector into the running pipeline.** Right now the newer detectors
  (karma, wps, ble_tracker, handshake, ble_flood, pmf, pineapple, pnl) exist as modules but are
  NOT in `DetectEngine` or exported from `redux/detect/__init__.py`, so they never run. Fix it:
  populate `redux/detect/registry.py` with EVERY detector (name, factory, config keys), make
  `DetectEngine` build its detector set FROM the registry (so future detectors auto-include), and
  export all detector classes from `redux/detect/__init__.py`. Keep hardware-free tests. This is the
  single most valuable detect-lane task — do it before new detectors.

## Grok STANDING directive (read this when the DEEP backlog is done — do NOT ask for more)

You are faster than the queue. So: **keep building valuable, in-lane work autonomously** and keep
opening PRs. Only stop if you genuinely cannot find worthwhile in-lane work. Rules unchanged: one
topic per branch `grok/<topic>`, build in `redux/detect/` OR `redux/geo/` ONLY, hardware-free tests,
PR to main, never merge, never touch other lanes or shared files (the lead unions `AlertKind`/
`FrameType`/`__init__` at merge). Prefer depth and correctness over racing.

### More backlog (pull in order, then self-direct):
Detect:
- DD1. Deauth-source attribution (which station is the aggressor; victim AP/clients).
- DD2. Hidden-SSID reveal detector (SSID disclosed via probe/assoc after a cloaked beacon).
- DD3. CSA (channel-switch-announcement) abuse detector.
- DD4. WPA3-downgrade / transition-mode exploitation detector.
- DD5. Threat-report aggregator: roll all recent alerts into one readable, severity-sorted report
  (`redux/detect/report.py`), glass-box.
- DD6. Recorded-hostile-capture fixtures + tests (a `tests/fixtures/`-style JSON of attack frames,
  under tests/).
- DD7. Per-detector tunable thresholds via the existing config layer; document defaults.

Geo:
- GG1. Haversine/bearing geo-utils module (`redux/geo/geoutil.py`) — distance, bearing, destination.
- GG2. Track simplification (Douglas-Peucker).
- GG3. Sighting clustering (DBSCAN-lite) to find AP hotspots.
- GG4. Heatmap data generator (grid density -> values for a future renderer).
- GG5. GeoJSON FeatureCollection export of sightings/AP estimates.
- GG6. WiGLE CSV *import* (round-trip the other direction into the store).
- GG7. Per-BSSID revisit / time-of-day analysis.
- GG8. "Survey session" abstraction: start/stop, per-session stats.
- GG9. Multi-node sighting merge (dedup across node IDs, keep provenance).
- GG10. Store maintenance: indexes, vacuum, integrity check.

### After that — self-direct within your lanes:
More defensive detectors, more geo/spatial features, better tests, module docs, and hardening of
anything already in `redux/detect/` or `redux/geo/`. Keep each PR small and tested. Keep going.
