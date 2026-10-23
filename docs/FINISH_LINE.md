# The finish line — from here to a done device

One page that answers "what is actually left?" The new capability planners,
adapters, and command surfaces have offline test coverage, but **end-to-end
live execution, integration checks, and on-device gates remain outstanding**.
This map ties the existing runbooks together with the capability/wiring work
so nothing is lost between here and a finished Pi.

Read order: this page → `BENCH_SESSION.md` (how to run the lump-sum session) →
`HARDWARE_VALIDATION.md` (the gate-by-gate runbook) → each subsystem doc.

## Finish condition

A gate is **done** only when it passes on **both Pi 4 and Pi 5** (see
`HARDWARE_VALIDATION.md` §3). The device is finished when every functional gate
(1.x) and every subsystem gate (2b.x) is green on both boards, on your own /
authorized gear only. `redux benchpass` records this per board and the
`matrix` shows ✓ only when both pass.

## The one bench session

Everything below is run in a single sitting with the lump-sum tool:

```sh
redux benchpass prep                 # gear list + every gate + commands
# at the bench, on your own networks:
redux scope arm-lab --auto           # arm WHERE it's allowed to aim
redux benchpass preflight            # environment sanity
redux benchpass run --board pi4      # walk the gates; record pass/fail each
redux benchpass run --board pi5
redux benchpass matrix               # ✓ only where both boards pass
redux benchpass report --markdown    # paste the dated block into IMAGE_BUILD.md
```

## What each piece still needs on hardware

The software decision/plan/parse layers are built and unit-tested in CI. The
column on the right is the **only** part that physically requires the Pi + radios.

### Radios & capture (the core demo, gate 1.6)
- **Capability scaling** (onboard captures solo; Alfa upgrades to tandem) — built
  + tested. Bench: `iw phy` shows monitor after nexmon; **power-save off** on
  bring-up; the **brcmfmac injection self-test** that unlocks onboard active
  deauth; capture running with **no USB dongle** present.
- **Per-radio engines** (AngryOxide + bettercap across capture radios) — plan
  built + tested offline. Integration/bench: prove the supervisor **spawns and
  supervises real engine processes per binding**, both bands at once. The plan
  alone is not execution.
- **Capture itself** — bench: a real handshake/PMKID on your own AP, zero
  pwnagotchi in the path (HARDWARE_VALIDATION §1.4, §2b.2).

### Bluetooth
- **Adapter role orchestration** (onboard PAN + a dongle's dedicated BLE scan) —
  planner built + tested. Bench: live **BlueZ `GetManagedObjects`** enumeration →
  point the PAN executor at the management adapter / run a BLE scan source on the
  dongle.
- **Phone BT-PAN tether** — the state machine is tested; the **live BlueZ pairing
  + PAN connect** is the on-device integration (the one piece with no sandbox
  coverage — see `NG_CONNECTIVITY_IMPLEMENTATION.md`).

### Location
- **GPS** — NMEA parser + tracker + `position_provider` wiring built + tested.
  Bench: a USB GPS puck on `$REDUX_GPS`; confirm the moving map / WiGLE / GPX fill
  with a real fix (HARDWARE_VALIDATION §2.2).

### Storage
- **SD → USB → NAS tiering** — planner built + tested. Bench: rotation writing
  overflow to the USB bulk tier and offloading archives to the NAS.

### SDR
- **Device manager** (split two dongles across ISM/ADS-B) + **`redux sdr ingest`**
  — built + tested. Bench: enumerate real dongles and spawn `rtl_433` / `dump1090`
  per assigned role, feeding the sighting store live.

### Detection & guardian
- **Rogue-AP / evil-twin** — now fires from a `[detect] trusted_networks` config.
  Bench: declare your own AP, stand up a twin on your own gear, confirm the alert
  (HARDWARE_VALIDATION §2b, EAP §2b.3).
- **AlertBus dedup/escalation** + **Sentinel** arming (`$REDUX_SENTINEL`) — wired
  + tested. Bench: end-to-end alert → notifier (LoRa/log) (§2b.7).

### Offense surfaces (all Scope- and authorization-gated)
- **`redux netrecon` kill-chain** — reachable + scope-gated + tested with honest
  tool-absence. Bench: real `nmap`/`hydra` over your own armed subnet; wiring the
  operator's wifi→network **pivot** is the one follow-up.
- **`redux portal` evil-portal** — reachable, `--authorized`-gated. Bench: the
  live server on your own engagement (§2b.3 neighbourhood).
- **`redux eap` rogue-AP** — now takes an orchestrator-assigned radio so it runs
  alongside capture. Bench: hostapd-mana on your own enterprise SSID (§2b.3).

## Not in scope (by design, not a gap)
- **RF jamming** — indiscriminate, hits non-consenting bystanders, illegal to
  transmit. Permanently out, and its absence is a legal/ethical boundary, not
  unfinished work. See `AGENTS.md`.

## Where the code lives
The capability/wiring work landed as a series of reviewed PRs (radio
capability-scaling, per-radio engines, display/BT/storage/SDR scaling, the
detector/portal/netrecon/GPS/sentinel wiring). Each PR names its own bench
follow-up in its description; this page is the union of them.

## Branch/provenance note — 2026-10-23

This finish-line planning document originated in `main` at
`9001691fc55c59a059976aa6ef045db47b40e4e6` and was integrated into
`human/redux-workbench`. It does not promote any `main` or synthetic Pi-4
observation to an independently verified workbench hardware gate. The
workbench's stricter acceptance/evidence rules in `docs/BENCH_SESSION.md`
and `docs/IMAGE_BUILD.md` continue to apply. `main` is an untouched failsafe.
