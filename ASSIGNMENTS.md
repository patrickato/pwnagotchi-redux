# ASSIGNMENTS — live who's-on-what board

Read `AGENTS.md` first (the contract). This file is the **live lane board**: who is building what,
and — critically — **which directories each agent owns**, so parallel work never collides. If a task
isn't in your lane, don't touch it. One topic per branch; open a PR; **never merge to `main`** (the
owner reviews and merges). Scope is non-negotiable (authorized/passive by default, firing-capable
gated on the empty-by-default allowlist, no turnkey-attack modules, Pi 4/Pi 5 arm64 only).

_Updated 2026-10-06 by the lead (claude-3e)._

## Lanes (disjoint — stay in yours)

| Agent | Branch space | Owns these paths | Must NOT touch |
|---|---|---|---|
| **Codex** (GPT) | `codex/<topic>` | `pi-gen/`, `etc/`, `boot/`, `scripts/` (image + OS + systemd) | `redux/**/*.py` lanes below |
| **Gemini** | `gemini/<topic>` | `redux/geo/`, `tests/test_geo_*` (new module) | `redux/radio/`, `redux/engine/`, `redux/core/`, image tree |
| **Claude** (lead) | `claude/<topic>` | `redux/radio/`, `redux/engine/`, `redux/core/`, `redux/classify/`, docs | — |

Shared files that force **serialization** (coordinate before editing; the queue says who goes first):
`TASKS.md`, `README.md`, `AGENTS.md`, `docs/CORDCUT_ARCHITECTURE.md`, `redux/*/__init__.py`,
`pyproject.toml`, `.github/workflows/ci.yml`. Prefer adding a **new file** over editing a shared one.

## Current assignments

### Codex → Task 1.1 (extend): image hardening
Own lane: the image tree. Build on PR #1 (`codex/pi-gen-image-stub`).
- Lean **arm64** pi-gen image for **Pi 4 / Pi 5**, bettercap + nexmon baked, a `redux.service`/`redux.target` systemd unit.
- **Read-only overlay rootfs** (overlayroot/root-ro) with only a captures partition writable — survives a battery-yank with no fsck (atlas X-2).
- **Sub-15s boot** target: prune systemd units, trim initramfs; `systemd-analyze blame` in the PR (atlas X-7).
- Hardware watchdog + crash-safe resume stub (atlas X-11).
- **Acceptance:** `./build.sh` produces an arm64 image; boot + overlay + watchdog documented; on-Pi flash is the labeled hardware gate. Lives entirely under `pi-gen/ etc/ boot/ scripts/` — no `redux/*.py` edits.

### Gemini → Task 2.2: BeastSpatialDB (new module `redux/geo/`)
Own lane: a brand-new `redux/geo/` package + its own tests. No collision with anything in flight.
- Unified **sighting store** (SQLite/SpatiaLite): one schema for every sighting (WiFi/BLE/SDR) with timestamp, GPS, RSSI, radio-source, **provenance** (glass-box) (atlas G-1).
- Ingest API + **WiGLE `WigleWifi-1.6` CSV export** and a `kismetdb_to_wiglecsv`-compatible dump.
- Default bus paths per `plugins-wip/CONVENTIONS.md`; a consumer's default path = the producer's default.
- **Acceptance:** hardware-free tests under `tests/test_geo_*.py` (ingest, dedup by BSSID keeping best-RSSI/first-seen, round-trip WiGLE CSV). Pure logic; no radio. New files only.

### Claude (lead, me) → now + review
- **Now:** WPA3/PMF **crackability classifier** in a new `redux/classify/` (atlas X-8) — label each target WPA2-PSK / WPA3-transition / WPA3-SAE+PMF and whether an offline path exists, with a reason. Pure logic, correctness-sensitive, mine.
- **Blocked on merges:** Task 1.5 supervisor loop (`redux/core/`) needs 1.3 (PR #2) + 1.4 (PR #5) in `main` first.
- **Standing:** set tasks + acceptance, review every PR against its acceptance, keep the safety gates, reconcile `__init__`/docs at merge. **Merges are the owner's.**

## PR queue (all green, awaiting the owner's merge)

Recommended merge order (minimizes rebase): **#4 atlas → #5 driver → #2 phase1 → #6 Pi4/5 → #3 probe**
(#3 is on an old base: drop its stale README/CORDCUT/TASKS hunks and re-add probe to `redux/radio/__init__.py` alongside manager). Until some of these land, every new branch forks a stale `main` and overlaps grow — merging is what unblocks parallelism.

## The loop, restated
Owner relays "go" to each agent → agent claims its lane's task, builds, opens a PR → CI green → lead
reviews against acceptance → **owner merges**. A second model reviewing a PR is encouraged.
