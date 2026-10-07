# Beastagotchi — platform vision

_2026-10-06. Grounded in a read of jayofelony/pwnagotchi 2.9.5.8 source, not memory. A thesis for turning Beastagotchi from "a better pwnagotchi" into its own thing._

---

## What's actually in jayofelony 2.9.5.8 (the ground truth)

| Piece | Reality in the current fork |
| --- | --- |
| The "AI" | **Gone.** No `ai/` package. The old A2C reinforcement-learning brain was dropped (old ML deps wouldn't build on modern ARM/Bookworm). |
| `strategy/channels.py` | Its replacement: scan active channels + **N random** unscanned ones to explore. It records per-channel Associations/Deauths/Handshakes in `ChannelStatistics`… and then **doesn't use those stats to choose channels.** Greedy-active + random. |
| `epoch.py` | Still computes, every epoch, the **full observation** (`aps/sta/peers` histograms per channel) **and the full reward signal** (handshakes, assocs, deauths, misses, bond, blind/sad/bored/active, cpu/mem/temp). |
| `automata.py` | The "mood" engine — fixed thresholds mapping activity → emotional states → faces + plugin hooks. Pure heuristic. |
| plugins | A large, mature `on_*` event bus (on_epoch, on_handshake, on_channel, on_peer_detected, on_bcap_wifi_ap_new, on_ui_update, …). The real extension surface. |
| mesh/grid | pwngrid peer-to-peer ("bond" when units meet). Present, underused. |
| base | bettercap 2.40.1 (6 GHz now), nexmon for kernel 6.18, pcapng captures, pwngrid. Solid radio plumbing. |

**The gift:** the brain socket is still wired. `epoch.py` computes the exact observation + reward a learning policy needs, every epoch — and `strategy` throws per-channel outcome data away when it decides. A real brain can drop into a loop that already feeds it, with almost no surgery. The old AI was removed; the *plumbing it used was not.*

**The chronic pain:** plugin rot. Half your own audit repo exists because community plugins subclass a `BasePlugin` that doesn't exist, call dead hooks, or import missing modules. The ecosystem's daily frustration is "plugins that don't load." Whoever fixes *that* wins goodwill the feature list can't buy.

---

## The thesis

Pwnagotchi is a single-radio, 2.4 GHz, handshake-collecting toy with a dead brain and a fixed personality. Everyone who loves it is loving the *character*, not the capability — the capability has barely moved in years.

Don't ship a cuter handshake toy. Ship the thing pwnagotchi gestured at and never became: **a field-awareness brain for your whole RF neighborhood — multi-radio, genuinely learning, able to tell you what's happening and where, and legible about why.** That's Beastagotchi's natural destiny, and nothing in this scene actually occupies that space.

---

## Fork, or its own image?

**Its own image — with a pwnagotchi-plugin compatibility shim, on the proven bettercap/nexmon base.**

- A *fork* inherits pwnagotchi's ceiling and, worse, its *perception* — "oh, another pwnagotchi mod," which is exactly the dismissal you're tired of.
- A *standalone platform* gets its own identity and roadmap, **but** a compat shim lets it still load the existing plugin universe on day one (huge head start), and reusing bettercap/nexmon/pwngrid means you don't reinvent the hard radio plumbing that already works.
- Net: new brain, new spatial/multi-radio core, new stable plugin contract — bolted onto the battle-tested capture layer, speaking pwnagotchi-plugin as a dialect.

---

## The pillars (each kills a real gap)

| Pillar | What it is | The gap it kills |
| --- | --- | --- |
| **Glass-box brain** | A contextual bandit (Thompson/UCB) over channel/dwell/behavior, fed by the existing epoch observation + per-channel outcomes + location/time/history. It can say *why* it's on ch 6 right now. | The old AI was a black box people couldn't trust or tune; this one is legible and actually uses the data the device already collects. |
| **Multi-radio fusion** | WiFi + BT/BLE + sub-GHz/SDR onto one spatial picture (the Beast spine). | pwnagotchi is mono-WiFi. Fusing the whole RF neighborhood is a category leap, not a feature bump. |
| **Spatial + temporal** | GPS + history + direction-finding: *where* and *when*, mapped; "it's that way," not "something happened." | pwnagotchi has no sense of place; everything is a faceless counter. |
| **Stable capability contract** | Typed Signal/Event/Action/Capability interface + a curated, tested plugin store; pwnagotchi-plugin compat shim. | Plugin rot — the #1 daily pain. A contract that doesn't break + vetted plugins is a "must-have," not a nicety. |
| **Red + blue in one** | Recon *and* the attacker-source locator (who/what/where, live), all authorized/scoped. | Everything else picks a side; a legible device that does both is memorable. |
| **Fleet as first-class** | Units share one picture (your Pi4 + Pi5), distributed situational awareness over pwngrid. | pwngrid exists but is a novelty; make coordination a headline. |
| **Shareable output** | Auto-generated maps/reports/portfolio artifacts worth showing. | The thing that gets a project *remembered* is the artifact people screenshot. |
| **Identity** | The Beastagotchi character, your faces/themes, a device with a face and a voice. | The emotional hook that made pwnagotchi beloved — but yours, not evilsocket's. |

---

## The brain, concretely (first real build)

The old A2C is the wrong tool anyway: tiny action space, sparse/noisy reward, short on-device training — deep RL underperforms and is fragile there. A **contextual bandit** is the right fit and it drops into the loop that already exists:

1. `epoch.py` already hands you the observation (per-channel AP/STA/peer histograms) and the reward (handshakes/assocs this epoch). Nothing to build there.
2. `strategy/channels.py` already keeps per-channel outcome stats — it just doesn't *weight* selection by them. Replace "active + random" with "sample channels in proportion to expected capture value, with an exploration term."
3. Condition the value estimate on **context** from SpatialDB: time-of-day, GPS cell, historical density per channel here. It learns "ch 11 pays off at this spot in the evening" instead of starting cold every boot.
4. Keep it **glass-box**: the device surfaces its current belief ("dwelling ch6: 0.8 expected, exploring ch44") on the TFT and in the web UI.

Minimal surgery, no heavy ML stack, explainable, and measurably better than greedy+random — which is a low bar the old AI never actually cleared in practice.

---

## Why people remember it

- It **fixes the pain they actually have** (plugin rot) — that earns trust nothing else does.
- It's **legible** — a brain that shows its work, in a scene full of black boxes and mystique.
- It's a **category jump** (multi-radio, spatial) not a skin.
- It **produces artifacts worth sharing** — maps, located attackers, reports. Screenshots travel.
- It has a **face and a name** — the thing that made the original beloved.

That combination — solves-the-pain + legible + category-jump + shareable + character — is what turns "neat project" into "the one everybody links to."

---

## Honest risks

- **Scope is enormous.** This is a platform, not a weekend. The failure mode is boiling the ocean and shipping nothing. Phase it hard.
- **Real-hardware validation** gates everything (same rule as the plugin audit): sandbox-green ≠ field-proven.
- **Don't reinvent the radio layer.** Stand on bettercap/nexmon/pwngrid; spend your originality on the brain, the spine, and the contract.
- Everything offensive stays **passive/authorized-scoped** per the project's standing rules.

## Phased roadmap

1. **Spine** — SpatialDB + BeastPlot + alert bus + capability contract (the Beast Recon Suite spine; everything rides it).
2. **Glass-box brain** — the contextual bandit in the existing observe→strategy→reward loop. The headline differentiator, cheap to prototype.
3. **Multi-radio ingest** — SDR/BT feeders onto the spine (RF Fusion, rtl_433).
4. **Compat shim + plugin store** — load existing pwnagotchi plugins; curate/test a trusted set (your audit work *is* the seed).
5. **Red+blue + fleet + shareable reports** — the locator, pwngrid fleet picture, auto-reports.
6. **Identity pass** — faces/themes/voice; make it unmistakably Beastagotchi.
