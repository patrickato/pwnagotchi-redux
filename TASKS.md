# TASKS — the build queue

One row per scoped unit of work. An agent claims an **open** task, works in its branch space, and
opens a PR that satisfies the acceptance criteria. Lead moves it to **review** then **done**.
Status: `open` / `claimed:<agent>` / `review` / `done (sandbox)` / `done`.
`done (sandbox)` = merged to `main` and green under `pytest`, but **not yet validated on real
Pi hardware**; `done` = also passed a real-hardware pass. (See README *Verification status*.)

## Phase 0 — scaffold (this commit)

| # | Task | Acceptance | Status |
|---|---|---|---|
| 0.1 | Repo scaffold, CI, AGENTS, docs | `pytest` green, CI runs on PR | done |
| 0.2 | Radio Orchestrator decision engine | `tests/test_orchestrator.py` green; glass-box reasons on every role | done |

## Phase 1 — cord-cut MVP (flashable v0.1)

| # | Task | Acceptance | Status |
|---|---|---|---|
| 1.1 | pi-gen image stub (lean Debian/Pi-OS, arm64, bettercap + nexmon baked, redux service) | `./build.sh` produces an arm64 image that boots to a redux service on a Pi 4 / Pi 5 | open (codex #1) |
| 1.2 | Radio Orchestrator live layer: `iw phy` capability probe → `Radio` records | on a Pi, enumerates onboard + a plugged adapter with correct bands/monitor/inject | in main (#3): iw phy/dev parser → Radio records; 3 tests. On-Pi enumerate = on-Pi gate |
| 1.3 | Radio Orchestrator udev hotplug → `on_hotplug`/`on_unplug`, apply monitor up/down | plug/unplug an adapter mid-run; roles re-apply with no manual steps | in main (#2): manager + plan_transition + hotplug entrypoint + udev rule/hook; 17 tests. Live iw/ip apply = on-Pi gate |
| 1.4 | `BettercapDriver` live client (REST/ws), `set_interface`, event stream | capture one handshake on an authorized/own AP with zero pwnagotchi code in the path | in main (#5): REST driver + injectable transport, glass-box events, empty-by-default allowlist firing gate, ReplayTransport for CI. Live capture on a real AP + ws stream = on-Pi gate |
| 1.5 | Supervisor loop wiring orchestrator + driver + creature screen | intent switch re-arranges radios + repoints bettercap, shown on the TFT | in main (#10): decision/wiring core — intent/hotplug → decide → radio apply → repoint bettercap at the capture radio, glass-box reasons, injected RadioControl/Driver protocols. Creature TFT + timed loop = on-Pi gate |
| 1.6 | The demo: flash, plug Alfa, auto-arrange + capture, hands-off (record a clip) | reproducible on a real Pi 4 / Pi 5 | open |

## Phase 2 — fusion, brain, packs (see docs/ for detail)

| # | Task | Acceptance | Status |
|---|---|---|---|
| 2.1 | Port Beastcore spine (signals/actions/transactions/spec_registry) into `redux/core` | ported modules pass their carried-over tests | done (sandbox) — spine assembles on one bus; spec-registry role covered by `ActionRegistry`+`ActionSpec`+`specs()` |
| 2.2 | BeastSpatialDB + multi-radio ingest (recon suite) on the bus | sightings persist; map view renders real data | done (sandbox) for DB + ingest + spatial suite (`redux/geo`); **dedicated geographic map panel still TBD** (web dashboard renders status, not a map) |
| 2.3 | Glass-box brain v1 (orchestration + explainability; NOT channel-yield RL) | brain explains each decision; no regression vs greedy on capture | done (sandbox) — every recommendation carries a human-readable reason; capture A/B is a real-hardware claim, unverified |
| 2.4 | Beast Packs incl. opt-in Kali-tools repo; pwnagotchi-plugin compat shim | a pwnagotchi plugin loads via the shim; a pack installs/removes cleanly | done (sandbox) for pack manager + pwn-shim; **opt-in Kali-tools pack repo itself still TBD** |

> Scope reminder (AGENTS.md): authorized/passive by default; firing-capable work needs the
> empty-by-default allowlist gate and only lands with the lead's explicit sign-off.
