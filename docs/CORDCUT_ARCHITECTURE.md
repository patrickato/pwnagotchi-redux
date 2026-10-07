# Cord-cut platform architecture — a standalone field OS, not a pwnagotchi fork

_2026-10-06. Grounded in reads of jayofelony/pwnagotchi 2.9.5.8 and patrickato/beastagotchi integration/v0.19. Working codename only; final name is yours (a few options at the end)._

## The decision, locked

Stop building on the jayofelony image. Build **our own image** on the same open foundations pwnagotchi itself stands on, delete pwnagotchi's Python wrapper, and put **Beastcore in its place as the supervisor**. Same capabilities or better, because the capabilities were never pwnagotchi's — they were bettercap's and nexmon's all along.

## The stack (what replaces what)

| Layer | pwnagotchi today | Cord-cut platform |
| --- | --- | --- |
| Base image | Raspberry Pi OS + their pi-gen | **Our pi-gen**: lean Debian/Pi-OS stable, **arm64, Pi 4 / Pi 5 only** (no Zero/3 or earlier), fast boot, SD-friendly. Kali's tools available as opt-in **Beast Packs / apt repo**, never preinstalled. |
| Radio | manual iface juggling | **Radio Orchestrator** (the headline — below) |
| Capture engine | bettercap (via their supervisor) | **bettercap, driven directly** by Beastcore over its REST/websocket API |
| Supervisor | pwnagotchi Python (agent/automata/epoch) | **Beastcore** (promoted from consumer to supervisor) |
| Brain | removed A2C / greedy strategy | **Glass-box brain** seated in Beastcore (honest scope — below) |
| UX | fixed faces + plugins | **Beast creature + Studio + Packs** (already built) |
| Extend | pwnagotchi plugins | **Beast Packs + pwnagotchi-plugin compat shim** |

The only two pieces we deliberately reuse rather than rewrite are **bettercap** (the capture engine) and **nexmon** (onboard-chip monitor/injection firmware). Rewriting either is years of work with zero differentiation. Everything above them is ours.

## The Radio Orchestrator (the feature that sells it)

The pitch: **you never touch monitor mode again.** You tell it what you're doing; it arranges the radios.

**1. Detect.** A udev hook fires on any adapter hotplug/unplug (`ACTION=add/remove`, `SUBSYSTEM=net`/`usb`). No polling, no reboot.

**2. Probe capability** (not guesswork): `iw phy` tells us per-adapter supported bands (2.4/5/6 GHz), whether it supports `monitor`, and the driver/chipset (→ whether injection is realistic). Each adapter becomes a typed capability record: `{phy, bands, monitor:bool, inject:likely, driver, usb_gen}`.

**3. Assign roles from intent, automatically.** The user (or the brain, from context) picks an **intent**, and the orchestrator maps radios → roles to satisfy it:

| Intent | What the orchestrator arranges |
| --- | --- |
| **Online** | one radio = managed client (internet/uplink); others idle/scan |
| **Hunt** | best injection-capable radio → monitor + handed to bettercap; keep one radio on uplink if available |
| **Recon** | passive monitor only, no injection (quiet/legal survey) |
| **Survey** | monitor + GPS for wardrive/mapping |

**4. Hotplug promotion/fallback, no steps.** Plug the Alfa in mid-hunt → orchestrator sees MT7612U is a stronger capture radio than the onboard, promotes it to monitor, moves the onboard to uplink/idle, and tells bettercap to switch interfaces — live. Unplug it → graceful fallback to onboard. You did nothing.

**5. Power/health awareness.** It knows real failure modes (your Alfa browns out on a shared hub / forced USB 2.0) and *warns* — "put the Alfa on its own USB 3 port" — instead of silently faulting. Detects `clear-tt` USB errors and throttling.

**6. Honest limits, surfaced not hidden.** 6 GHz / 6E monitor+injection on Linux is adapter- and driver-limited and regulatory-gated; the orchestrator detects what a radio *actually* can do and degrades gracefully, telling you why, rather than pretending. (Note: jayofelony 2.9.5 already added 6 GHz via bettercap 2.40.1 — so our differentiator is *robust, automatic* multi-band, not "we have 6 GHz.")

This is a real `beastcore/radio_orchestrator.py` service + a `collectors/radio.py` upgrade + `actions` for mode changes + a Studio panel showing the live radio map. It's maybe the single most demoable thing in the project.

## Beastcore, promoted

Keep everything it already is (signals, actions, transactions, spec_registry, dependency_resolver, packs, doctor, experience_dna, platform_profile, collectors). Add the supervisor role it didn't need when pwnagotchi was underneath:

- **`bettercap_driver`** — owns the bettercap process + its API; the capture/recon/handshake surface. Replaces the pwnagotchi agent/epoch loop.
- **`radio_orchestrator`** — above.
- **Brain seat** — a policy module that makes the decisions pwnagotchi's automata faked: intent inference, sleep-vs-hunt, when to suggest moving, multi-radio prioritization. (See honest scope below.)
- **Beast spine hooks** — BeastSpatialDB + the recon suite ride the same data bus; Beastcore is the read/write hub.

Your README's "protected engine" principle survives intact — it just protects **bettercap** directly instead of pwnagotchi.

## The brain — honest scope (carried over from today's sim)

We proved it: a learning policy does **not** reliably beat greedy+random at channel selection (it can be worse — channel capture is a restless/depleting problem, and dumb coverage wins). So the brain is **not** sold as "more handshakes." Its real, defensible jobs:

1. **Orchestration intelligence** — infer intent, manage radios/power, decide sleep vs hunt. Decisions greedy never made.
2. **Glass-box legibility** — always shows *why*. The anti-mystique differentiator.
3. **Depletion-aware revisit scheduling** — the one capture-optimization worth testing honestly (coverage + regen timing), A/B'd before any claim.

## Scope discipline (so it ships, and so it's clean)

- **Lean base + packs.** The default image is small and reliable; "kali-everything+" is opt-in. This is your own stated philosophy.
- **Authorized/passive by default; turnkey everywhere else.** Radio capabilities default to receive/recon; any firing-capable capability keeps the empty-by-default allowlist gate (repo rule). Ship turnkey modules when possible — the only thing we don't ship turnkey is a *turnkey attack/weaponized* module (one-click-to-fire); make everything legitimate as one-click as it can be.
- **Ship a narrow, stunning v1**, let the giant vision be the roadmap behind it.

## Phased build

1. **v0.1 cord-cut MVP** — our pi-gen image; Beastcore drives bettercap directly (capture a handshake with zero pwnagotchi code in the path); Radio Orchestrator doing auto monitor/role on onboard + one adapter; the creature + a live radio-map panel. **The demo:** flash it, plug in the Alfa, watch it auto-arrange and start capturing — hands-off.
2. **v0.2** — multi-radio fusion onto the Beast spine; GPS/survey; glass-box brain v1 (orchestration + explainability).
3. **v0.3** — Beast Packs incl. the Kali-tools repo; pwnagotchi-plugin compat shim; depletion-aware scheduler A/B.
4. **v0.4** — fleet, shareable reports, identity/packs polish.

## Name (your call — a few directions, not "-gotchi")

Something that says multi-radio field-awareness + living instrument. E.g. **Beacon**, **Lumen**, **Fathom**, **Oracle**, **Quill**, **Sentience**/**Sentry**-root plays, or keep the **Beast** lineage as the creature inside a new product name. Final call is yours.
