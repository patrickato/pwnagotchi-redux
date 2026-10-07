# IDEA ATLAS — the broad sweep (nothing here is approved to build)

_A grounded survey of what the adjacent ecosystems do (2024–2026) and what redux could take or
beat. Every entry names real prior art. This is a thinking map, not a plan: an idea graduates only
when it becomes a scoped row in `TASKS.md` with acceptance criteria and the owner's go. The curated
short-list of already-fleshed ideas lives in `docs/IDEAS.md`; this file is the wide net behind it._

## Scope (inherited, non-negotiable)

Ship **turnkey** — one-click, automatic — wherever we can; that's the whole pitch. The single
exclusion is a **turnkey attack/weaponized module** (one-click-to-fire). Anything firing-capable
(deauth, assoc, rogue-AP, HID inject, bruteforce, replay) is tagged **[authorized-gated]** below:
it gates on an explicit authorized-target allowlist (BSSID/SSID/device), **empty by default**, with
a dry-run/preview and a per-action glass-box reason — and lands only with the owner's explicit
sign-off. **[passive]** = receive-only, no transmit. **[infra]** = platform/plumbing.

## The benchmark to beat

**Bjorn** (infinition, ~15k★) is now the pwnagotchi successor everyone points at: it autonomously
chains recon → nmap → vuln → brute-force (SSH/FTP/SMB/RDP/Telnet/SQL) → file-steal, with **no
allowlist and no shown reasoning**. That is exactly redux's opening: **same reach, opposite
philosophy** — consent-gated, dry-run-first, and glass-box on every decision. Meanwhile jayofelony
ripped the RL "AI" out of mainline pwnagotchi (firmware instability + battery), so today's device is
an opaque handshake-eater with a face. The three loudest community pains — **handshakes captured but
never cracked**, **brcmf driver crashes/reboots**, and **TOML "config hell"** — are all things redux
can simply _not have_.

---

## Top of the stack (the cross-domain picks, in rough priority)

These are the ideas that recurred across multiple domains or that most define the product. Each
points to its full entry below.

1. **Glass-box capture → crack pipeline** (W-1 / X-1 / B-10) — the end-to-end loop pwnagotchi never
   closed: recon → (gated) capture → auto-handoff → crack → potfile, each step showing its reason.
   Kills the #1 community pain. **[authorized-gated]**
2. **bettercap events-stream as Augur's nervous system** (B-1) — consume the _whole_ `/api/events`
   feed, not just handshakes; one normalized glass-box bus every suite reads. The architectural
   keystone most other ideas ride on. **[infra]**
3. **The on-screen "why" log + creature mood driven by real telemetry** (P-4 / H-1) — the literal
   glass-box feature and the beloved-factor in one; moods carry a human reason ("excited — new PMKID
   on the bus"). redux's identity. **[passive]**
4. **Unified 22000 capture + WPA3/PMF-aware crackability triage** (X-1 / X-8 / P-7) — normalize every
   capture to hashcat 22000 at ingest, and _honestly label_ what's crackable (WPA2-PSK vs
   WPA3-SAE+PMF = no offline path). Stops wasting cycles; nobody integrates this. **[passive]**
5. **NAS/desktop offload cracker with brain-dedup** (X-3) — Pi stays a pure sensor; the QNAP/GPU box
   cracks via `hashcat --brain-server`; results flow back to the bus potfile over Tailscale.
   **[authorized-gated]**
6. **SpatialDB: one sighting store + live offline moving-map on the TFT** (G-1 / G-2) — every
   WiFi/BLE/SDR sighting in one geo schema with provenance; a PMTiles offline basemap with an RSSI
   heat layer redrawn as you drive. No existing tool does live + offline + multi-radio on-device.
   **[passive]**
7. **Multi-device role orchestration + RSSI trilateration** (M-2 / G-3 / P-3) — the owner's core
   fascination: one node captures while another covers channels; fuse the same BSSID's RSSI across
   nodes to locate the transmitter. **[passive]** (coordinated firing is M-13, **[authorized-gated]**)
8. **Self-healing firmware watchdog + yank-safe read-only rootfs** (P-5 / X-2 / X-11) — the device
   survives brcmf wedges and battery-yanks with no corruption and a logged cause. Reliability is a
   feature. **[infra]**
9. **The allowlist firing-gate, shipped as the one legible gateway** (X-9 / H-2) — promoting a recon
   row to the authorized-target list is the single, visible doorway between passive recon and any
   gated action. The safety model _is_ a UX feature. **[authorized-gated]**
10. **Session record/replay for hardware-free CI** (B-12) — bettercap `api.rest.record/replay` lets
    suites be tested against real captured sessions with no radio. Serves the sandbox-vs-hardware
    split directly. **[infra]**

---

## P — pwnagotchi ecosystem & successors

- **P-1. Glass-box capture→crack pipeline** — close the loop pwnagotchi leaves open (caps a
  handshake, never cracks it). Prior art: `rekonnected/pwnagotchi-tools` bolts on offline hashcat.
  Edge: Augur runs it end-to-end on-device or offloaded, every target logged with a reason.
  _[authorized-gated] · M · bus: produces `crack_house_ng.potfile`, consumes timer CSV + captures._
  (See also X-1 for the 22000 ingest detail.)
- **P-2. Allowlist-gated autonomous network module (the Bjorn answer)** — once on a cracked net, scan
  hosts/services and attempt _gated_ actions. Prior art: Bjorn (fully autonomous, zero gate). Edge:
  refuses to fire outside an explicit BSSID/SSID + host allowlist, **previews a dry-run plan first**,
  prints a reason per action. The glass-box inverse of Bjorn — explicitly **not** a turnkey attack
  module. _[authorized-gated] · L · bus: produces a host/service table._
- **P-3. Multi-radio / multi-device role orchestrator** — one node deauths (gated) while another
  captures. Prior art: `pwngrid` (dot11 peer novelty), `meshpwnstic` (Meshtastic LoRa remote).
  Edge: Augur as a real supervisor, not a mesh gimmick. _[authorized-gated] · L · bus: shared
  capture + status._ (Trilateration half → M-2/G-3.)
- **P-4. On-screen "why" decision log** — every channel hop / target pick shows a one-line reason.
  Prior art: none (pwnagotchi's removed RL was always opaque). The signature differentiator, cheap.
  _[passive] · S · bus: none._
- **P-5. Self-healing firmware watchdog** — Augur watchdogs bettercap/nexmon, restarts on a
  brcmf wedge, logs the cause. Prior art: the `fix_brcmf` plugin exists _solely_ to stop reboots.
  Edge: supervisor-native resilience, not a band-aid. _[infra] · S/M · bus: none._
- **P-6. GPS wardriving + WiGLE auto-upload + per-capture geotags** — Prior art: `gps_more`,
  `webgpsmap`, wigle plugins, `f0xtr0t`/`pwnamap` — heavily used but fragmented. Edge: geotag _every_
  capture onto the bus automatically. _[passive] · M · bus: produces GPS sidecars._ (→ G-1/G-4.)
- **P-7. Crackability triage / scoring** — rank which caps are worth cracking (has PMKID? vendor
  default-SSID pattern? already in potfile?). Prior art: none — novel. _[passive] · M · bus: consumes
  captures + potfile._ (→ X-8 for the WPA3 honesty layer.)
- **P-8. PMKID-first clientless capture** — prefer clientless PMKID (no deauth = stays passive) and
  _show_ when it falls back. Prior art: pwnagotchi does PMKID but indiscriminately. _[passive] · S ·
  bus: feeds pipeline._ (→ B-4.)
- **P-9. BLE recon correlation** — log BLE devices, correlate with WiFi + GPS. Prior art: `blemon`
  (counts only). _[passive] · M · bus: produces `bluetooth_recon_ng.json`._ (→ B-2/H-7.)
- **P-10. Cracked-password + QR display** — Prior art: `display-password` (popular). Edge: reads the
  pipeline potfile, not a side DB. _[passive] · S · bus: consumes potfile._
- **P-11. Good remote web UI (config + live map + gated toggles)** — Prior art: pwnagotchi "config
  hell"/webcfg, Bjorn's web UI. Edge: one pane, `bind_scope`-aware, always prints its exact URL.
  _[infra] · M · bus: reads all._ (The config-hell cure; see also H-13 catalog.)
- **P-12. Off-grid LoRa command + status** — Prior art: `meshpwnstic`. Edge: Augur command bus
  over LoRa with reasons — fits the owner's half-mile-neighbor rural setting. _[authorized-gated] · M
  · bus: command/status._ (→ M-3/M-4.)

## B — bettercap, driven directly (what the wrapper left on the table)

pwnagotchi drives bettercap for only `wifi.recon/deauth/assoc` + handshake pcap. Everything here is
a real module/API (bettercap 2.4.0–2.41.5) it never touched.

- **B-1. Full events-stream as the nervous system** — consume all of `/api/events` (ws mode), not the
  handshake slice. Edge: one normalized glass-box bus; no pwnagotchi pre-filtering. _[infra] · M ·
  bus: PRODUCES the internal event feed every suite consumes._ **Keystone.**
- **B-2. BLE recon + GATT enumeration** — `ble.recon/show/enum`, `BLEDeviceJSON`, `GET
  /api/session/ble`; pwnagotchi exposes zero BLE. `ble.write` gated. _[passive] (ble.write
  [authorized-gated]) · M · bus: produces `bluetooth_recon_ng.json`._
- **B-3. Cross-radio device graph** — bettercap's `graph` module (`graph.to_json/to_dot`, persistent
  DB, `graph.privacy`) joins APs/clients/LAN/BLE/GPS into one relationship map. pwnagotchi never
  touched it. _[passive] · L · bus: produces a correlated graph; consumes BT json + GPS._
- **B-4. Clientless PMKID-first capture** — `wifi.assoc` (RSN PMKID). Passive-first policy, deauth
  only on allowlist, logs the reason either way. _[authorized-gated] (assoc is active TX) · S · bus:
  produces handshakes._
- **B-5. pcapng-native capture with embedded metadata** — 2.41.5 moved pcap→pcapng; embed GPS +
  channel + run-id in name/comment blocks per packet. Richer provenance than legacy `.pcap`.
  _[passive] · S/M · bus: produces captures + `.gps.json`._
- **B-6. GPS as a first-class sidecar producer** — the `gps` module on every capture _and_ every BLE
  event. _[passive] · S · bus: produces `gps_tagger_ng/`._
- **B-7. HID / MouseJack recon + inject** — `hid.recon/sniff/inject` (DuckyScript; needs a CrazyRadio
  PA, e-waste-friendly). Recon passive; inject gated per-device with a per-frame log. _[authorized-
  gated] · M · bus: produces an HID device table._
- **B-8. Rogue-AP / evil-twin + ZeroConf (ZeroGod, new 2.41.0)** — `wifi.ap` beacon injection +
  mDNS/Bonjour service impersonation. One orchestrated "service-lure", allowlisted SSIDs, bind_scope
  + logged URL. _[authorized-gated] · L · bus: consumes allowlist._ (→ H-10.)
- **B-9. WPA3/cipher-aware classification** — 2.4.0 added WPA3/RSN cipher parsing; tag
  WPA2/WPA3-SAE/OWE/WPS so Augur skips futile deauth. _[passive] · S · bus: enriches the feed._
  (→ X-8.)
- **B-10. On-device bruteforce pipeline** — `wifi.bruteforce` (Linux, 2.4.0; workers/timeout). Pi4-
  resource-capped, allowlist-gated. _[authorized-gated] · M · bus: produces potfile entries._
- **B-11. Augur policy engine replacing Otto caplets** — bettercap scripting is Otto (ES5-only,
  no classes/typed arrays). Native declarative event→action rules instead: unit-testable, glass-box.
  _[infra] · M · bus: orchestrates all._
- **B-12. Session record/replay for hardware-free tests** — `api.rest.record/replay` snapshots a real
  session; replay in CI to exercise suites with no radio. _[infra] · S · bus: test fixture._
- **B-13. Smart channel dwell + CSA herding** — `wifi.recon.channel`, `wifi.hop.period`, current-chan
  in state (2.41.1); tune dwell per-AP activity; CSA only against allowlisted BSSIDs. _[authorized-
  gated] (CSA is active) · S/M · bus: consumes timer CSV._
- **B-caveats.** Out-of-core but real if scope ever widens: the `can`/OBD2 module (`can.fuzz`) for
  vehicle work; the IRC `c2` module — skip it, redux is cord-cut (prefer a Tailscale-scoped channel).
  **6GHz:** bettercap recon is radio/driver-bound and Pi4 + nexmon is effectively 2.4GHz — treat 6GHz
  as a hardware/driver question, **do not promise it as a feature.**

## G — wardriving / geospatial / RF mapping

- **G-1. SpatialDB unified sighting store + WiGLE/kismetdb export** — one geo table for every
  sighting (WiFi/BT/cell/SDR) with timestamp/GPS/RSSI/source/provenance. Prior art: Kismet
  `kismetdb` + `kismetdb_to_wiglecsv`, WiGLE CSV — each siloed/single-phy. Edge: one schema across
  all radios; emit `WigleWifi-1.6` CSV so it drops into WiGLE/Kismet pipelines. _[passive] · M · bus:
  PRODUCES the sighting DB (core new artifact); consumes GPS tags._
- **G-2. Live offline moving-map on the TFT with RSSI heat** — own track + AP markers + heat layer,
  fully offline. Prior art: Sparrow-wifi (needs a laptop), Kismet web UI (needs a browser),
  warmap-go (post-hoc). None do live + offline + on-device. Edge: Protomaps **PMTiles** single-file
  basemap + a 480×320 canvas renderer, monochrome-safe. _[passive] · L · bus: consumes sighting DB +
  GPS._
- **G-3. RSSI weighted-centroid AP geolocation** — estimate each AP's true position from accumulated
  RSSI-weighted sightings + a confidence ellipse. Prior art: WiGLE/Kismet (coarse, server-side/post-
  hoc). Edge: incremental path-loss centroid, error ellipse + observation count shown on the TFT.
  _[passive] · M · bus: consumes sighting DB → produces `estimated_location`._
- **G-4. gpsd tagging daemon + dead-reckoning gap-fill** — one gpsd consumer feeds every radio;
  `<capture>.gps.json` sidecars + continuous track; speed/heading fill when the fix drops (flagged
  low-confidence). _[infra] · S · bus: produces GPS tags._
- **G-5. GPS-speed-aware dwell scheduling** — long dwell when stopped, fast hop when moving; gpsd
  velocity feeds the Orchestrator, logs why each dwell changed. _[passive] · M · bus: consumes GPS._
- **G-6. Return-to-signal navigation** — a TFT bearing-arrow back to the point where a BSSID was
  strongest. Prior art: none navigate you back live. _[passive] · S · bus: consumes sighting DB._
- **G-7. Survey coverage / gap map** — grid the area; highlight covered vs unvisited cells to guide
  driving. _[passive] · S · bus: consumes GPS + sighting DB._
- **G-8. Offline self-geolocation from the known-AP DB** — fix own position from visible APs when GPS
  is lost. Prior art: Mozilla Location Service **shut down 2024** (so pwnagotchi `net-pos` is now
  broken); Google Geolocation is online/paid. Edge: match live BSSIDs against SpatialDB locally.
  _[passive] · M · bus: consumes sighting DB._
- **G-9. WiGLE dedup + incremental upload queue** — merge repeat BSSIDs (best-RSSI/first-seen),
  package only new rows, upload when online. _[passive] · S · bus: consumes sighting DB → produces
  WiGLE CSV._
- **G-10. True DF for an allowlisted target (KrakenSDR)** — phase-coherent DoA bearings fused with
  the GPS track to cross-fix one authorized target. Prior art: KrakenSDR (5-ch coherent RTL-SDR, DoA
  + heatmap). _[authorized-gated] · L · bus: produces bearing/fix rows._
- **G-11. Geo-tagged RF spectrum-occupancy mapping (SDR)** — sweep band utilization and geo-tag it.
  Prior art: Sparrow SDR spectrum, RTL-SDR heatmap scripts — none persist a geo layer. _[passive] · M
  · bus: produces a spectrum layer in the sighting DB._
- **G-12. Geofenced authorized-area gating + GPX/KML export** — a GeoJSON authorized polygon; any
  firing-capable module requires being _inside it AND_ on the allowlist (defense-in-depth, both empty
  by default). _[authorized-gated] · S · bus: consumes GPS._

## H — hardware feature inspiration (Flipper / Marauder / Pineapple / SDR)

- **H-1. Glass-box creature mood/XP engine** — moods/levels driven by real telemetry, each with a
  reason on the TFT. Prior art: Flipper's dolphin, pwnagotchi faces. Edge: transparent rules engine
  reading the whole bus. _[passive] · S · bus: consumes all producers._
- **H-2. Live tap-to-target recon dashboard** — AP/client table (BSSID/SSID/chan/RSSI/last-seen/
  clients); promote a row to a watchlist or the allowlist. Prior art: Pineapple Recon, Marauder live
  counters. Edge: promoting to allowlist is _the_ legible gateway to any gated action. _[passive];
  promote [authorized-gated] · M · bus: UI over every producer._
- **H-3. Flipper-style Read → Save → Emulate "cards"** — every artifact (handshake, probe profile,
  BLE adv, beacon set) becomes a named, provenance-stamped card you inspect and, if authorized,
  replay. Edge: replay is allowlist-checked; makes offensive capability a gated building block, never
  a button. _[passive] capture; [authorized-gated] replay · M · bus: produces named artifacts._
- **H-4. Wardriving mode + WiGLE CSV export** — Prior art: Marauder WiGLE CSV + NMEA. _[passive] · S ·
  bus: gps sidecars._ (folds into G-1/G-9.)
- **H-5. Probe-request PNL harvesting** — "who's looking for what" from device preferred-network
  lists. Prior art: Marauder probe sniffing, PineAP logging. _[passive] · S · bus: new producer the
  dossier reads._
- **H-6. Passive PMKID / EAPOL harvester** — Prior art: Marauder PMKID + EAPOL. _[passive] · S · bus:
  feeds potfile + timer CSV._ (folds into P-8/B-4.)
- **H-7. BLE proximity + AirTag/tracker + rogue-skimmer detection** — flag unexpected trackers and
  HC-05/06 skimmer modules. Prior art: Marauder "skimmer detection", Flipper BLE. Defensive, popular.
  _[passive] · M · bus: `bluetooth_recon_ng.json`._
- **H-8. Defensive "detector" pack** — deauth-attack / rogue-AP / evil-twin / surveillance-sweep
  detectors that raise a creature alert. Prior art: Marauder deauth detector, Flipper detectors. Pure
  defense, demos well, no legal risk. _[passive] · S · bus: consumer; emits alerts._
- **H-9. Channel waterfall + RSSI meter on the TFT** — live 2.4/5GHz activity waterfall + a hot/cold
  signal meter for locating. Prior art: Marauder waterfall, Portapack. Monochrome-safe sparkline.
  _[passive] · S/M · bus: none._
- **H-10. Authorized rogue-AP / captive-portal building block** — evil-twin/Karma + Evil Portal, but
  gated. Prior art: Pineapple PineAP/Karma, Marauder Evil Portal. _[authorized-gated] · M/L · bus:
  allowlist in, dossier out._ (= B-8.)
- **H-11. Deauth as a gated PMF-test building block** — deauth to verify 802.11w enforcement, framed
  as a test. Prior art: Marauder "deauth auditing", redux's own `wifiJtest` reference. _[authorized-
  gated] · M · bus: reads allowlist._
- **H-12. Scheduled "campaigns" with digest reports** — recurring recon runs producing a summary/diff.
  Prior art: Pineapple Campaigns. _[passive]/[infra] · M · bus: consumer; emits reports._
- **H-13. App/module catalog with one-tap install** — a browsable catalog of signed plugins. Prior
  art: Flipper Apps, Pineapple modules. Edge: this is exactly the `plugins-wip → complete-plugins`
  pipeline surfaced as a store; drives the ecosystem. _[infra] · M/L · bus: n/a._
- **H-14. SDR decoder suite (needs hardware)** — ADS-B (aircraft), POCSAG (pagers), AIS (ships), TPMS,
  APRS + a wide spectrum sweep. Prior art: HackRF PortaPack Mayhem, RTL-SDR. **Hardware:** RTL-SDR
  (~$30, RX-only) covers all decoders; HackRF (~$300) adds TX. _[passive] (RX) · L · bus: could add a
  device/sighting producer._
- **H-15. SDR capture → replay, sub-GHz/IR (needs hardware, gated)** — record IQ, inspect, replay
  authorized signals. Prior art: Flipper sub-GHz read/save/emulate, Portapack replay. **Hardware:**
  HackRF (TX). Folds into the card model (H-3); replay allowlist-gated. _[authorized-gated] · L · bus:
  produces replay cards._

## X — platform reliability & the cracking pipeline

**WPA3 reality up front (the honesty layer):** PMF (802.11w) is mandatory in WPA3, so deauth-forced
reassociation dies there; SAE derives the PMK via Dragonfly, so there is **no PSK-derived PMKID** and
the classic offline dictionary attack **does not apply to pure WPA3-SAE**. What's still live:
WPA2-PSK, and WPA3 **transition-mode** APs that keep a WPA2 path exposed. hcxdumptool 6.3.x
de-emphasized active attacks; hashcat mode **22000** (via `hcxpcapngtool`) replaced 16800/2500. Treat
this as the ceiling and _tell the user the truth per network_.

- **X-1. Unified 22000 capture→crack bus** — `hcxdumptool → hcxpcapngtool →` a single `.22000`
  hashline stream into a shared potfile. Edge: one canonical converter, every capture normalized +
  deduped at ingest with per-line provenance. _[authorized-gated] · M · bus: core producer —
  `crack_house_ng.potfile` + a cracked-networks JSON for dossier/viz._
- **X-2. Overlayfs read-only rootfs + yank-safe power-off** — rootfs read-only (overlayroot/root-ro),
  only a captures partition writable; survives battery-yank with zero fsck. Bake into the pi-gen
  image by default. _[infra] · M · bus: none (protects bus storage)._
- **X-3. NAS/desktop offload cracker with brain dedup** — Pi captures; QNAP/GPU box cracks via
  `hashcat --brain-server` (network dedup across sessions); results write back over Tailscale. Prior
  art: hashcat brain, GoCrack/HashKitty, pwnagotchi wpa-sec uploaders. _[authorized-gated] · M · bus:
  consumes captures, produces potfile._
- **X-4. Glass-box undervoltage/throttle monitor** — parse `vcgencmd get_throttled` to plain English
  on the TFT ("brownout at 14:32, capture paused") + log. _[infra] · S · bus: health event stream._
- **X-5. UPS HAT + graceful shutdown + SoC on TFT** — Prior art: Geekworm X1200/X728, PiJuice. Read
  state-of-charge, draw battery on the 3.5", clean overlay-sync shutdown at low threshold. Fits
  e-waste 18650 sourcing. _[infra] · S/M · bus: none._
- **X-6. A/B OTA image updates** — Prior art: RAUC (RPi5 port, FOSDEM 2025), Mender, SWUpdate. Because
  redux owns its image: signed RAUC bundles per release with auto-rollback on failed boot — real OTA,
  not `apt upgrade` roulette. _[infra] · L · bus: none._
- **X-7. Sub-15s fast-boot target** — a `redux.target` bringing up only radio + UI + supervisor;
  prune systemd units, trim initramfs. _[infra] · S/M · bus: none._
- **X-8. WPA3/PMF-aware capture classifier** — tag each target WPA2-PSK / WPA3-transition /
  WPA3-SAE+PMF and label offline-crackability truthfully, with a reason per network. Prior art: none
  integrated — most tools silently capture junk against SAE. _[passive] · S · bus: enriches the
  target JSON._ (= B-9 + P-7 combined.)
- **X-9. Authorized-target allowlist firing gate** — any assoc/deauth-capable action gates on an
  explicit BSSID/SSID allowlist, empty by default, `--disable_deauthentication` as the shipped
  default; low-friction allowlist UI on the TFT. The contract, made real. _[authorized-gated] · S ·
  bus: reads target list._
- **X-10. Constrained wordlist/rules tiers + NAS pull** — a tiny on-device tier (PSK patterns,
  SSID-derived, phone numbers) for instant hits; heavy lists stay on the NAS. Prior art:
  `pwnagotchi_fast_dictionary`, probable-wordlists, best64. _[authorized-gated] · S/M · bus: potfile-
  aware (skip already-cracked)._
- **X-11. Hardware-watchdog crash-safe resume** — BCM2835 watchdog + systemd `RuntimeWatchdogSec`;
  Augur resumes the capture session on reboot and replays unflushed captures to the bus. _[infra]
  · S · bus: none._
- **X-12. Opt-in online-crack offload** — wpa-sec / onlinehashcrack, strictly opt-in, glass-box
  "uploading N 22000 lines to X"; results merge into the same bus potfile. _[authorized-gated] · S ·
  bus: produces potfile entries._

## M — fleet / mesh / off-grid coordination (the swarm)

The owner's stated core fascination. Dependency spine: **time-sync (M-5) → trilateration/windows
(M-2, M-13); transport (M-7/M-8) → backhaul/merge (M-1, M-9); fusion DB (M-10) is the sink.**

- **M-1. Augur collector grid (Kismet-remotecap model)** — field Pis stream live frames to a
  central aggregator instead of hoarding. Prior art: Kismet remote capture (`kismet_cap_linux_wifi
  --connect`). Edge: glass-box — every frame tagged with node ID + synced time + GPS; writes the open
  bus, not an opaque DB. _[passive] · M · bus: produces merged timer CSV + sidecars keyed by node._
- **M-2. Multi-node RSSI trilateration / signal fusion** — fuse one BSSID's RSSI from 3+ nodes to
  locate the transmitter. Prior art: Kismet per-sensor signal; no pwnagotchi does cross-device
  fusion. Edge: `sigstr_ng` already reads the timer CSV per device — base joins all nodes, runs
  weighted-centroid, shows which nodes contributed. **The swarm differentiator.** _[passive] · L ·
  bus: consumes sigstr + gps → produces a target-location layer._
- **M-3. Reticulum (RNS/LXMF) encrypted control+telemetry mesh** — self-configuring E2E-encrypted
  mesh over any medium (LoRa RNode, WiFi, TCP). Prior art: markqvist/Reticulum + LXMF. Edge: survives
  when IP mesh drops; every message signed (glass-box identity). _[infra] · M · bus: syncs control +
  small alerts._
- **M-4. Two-tier comms: Meshtastic LoRa alert lane + WiFi bulk lane** — LoRa for heartbeats/alerts,
  WiFi/IP for bulk. Prior art: Meshtastic (Python API, MQTT module). Edge: LoRa carries "node X caught
  SSID Y" into Augur via MQTT→Mosquitto; files move over IP in range. _[passive] · S · bus:
  produces alert events referencing the potfile/BT entries._
- **M-5. Fleet time sync (chrony + GPS/PPS stratum-1)** — one GPS-disciplined Pi = offline stratum-1
  NTP; precondition for M-2 and M-13. _[infra] · S · bus: none (enables comparable timestamps)._
- **M-6. Coordinated channel-coverage orchestration** — Augur assigns disjoint channel sets per
  node so the fleet covers all channels without each hopping everything; rebalances on join/leave.
  _[passive] · M · bus: consumes roster → produces a per-node channel plan._
- **M-7. Secure capture backhaul to QNAP (Syncthing untrusted folders)** — captures sync home when
  connectivity returns; encrypted-at-relay, resumable, block-dedup. QNAP has a Syncthing package.
  _[infra] · S · bus: syncs all four artifacts to base._
- **M-8. Self-healing IP mesh backhaul (BATMAN-adv / Yggdrasil)** — nodes mesh so bulk data hops
  node→node→base with no infra; the transport under M-7 out of base range. _[infra] · M · bus:
  carries bus traffic._
- **M-9. Fleet-wide dedup + merge bus** — merge every node's potfile/BT table/handshakes into one
  master by BSSID/MAC, keeping all sightings (node/time/gps) per key. _[passive] · M · bus: consumes
  per-node artifacts → produces master merged artifacts._
- **M-10. Spatial fusion DB at base (SpatiaLite/PostGIS + H3)** — all sightings land H3-indexed for
  cross-device queries + heatmaps; feeds M-2. _[infra] · M · bus: consumes gps + merged artifacts →
  the queryable store._ (base-side big sibling of G-1.)
- **M-11. Live fleet + detection map via CoT/TAK** — node positions + detections on an ATAK/WinTAK
  map. Prior art: TAK + Meshtastic CoT forwarding. Edge: Augur emits Cursor-on-Target; a FreeTAK
  server at base renders the fleet. _[passive] · M · bus: consumes roster + detections → CoT stream._
- **M-12. Store-and-forward data mule (DTN)** — a roaming node physically carries captures from
  isolated nodes to base, syncs on contact, with a glass-box chain-of-custody per file. _[infra] · M
  · bus: syncs opportunistically._
- **M-13. Time-synced coordinated windows (authorized)** — the fleet runs synchronized capture, or
  allowlisted deauth, at an agreed timestamp (uses M-5's clock). Every firing action gates on the
  empty-by-default allowlist; deliberately **not** one-click-to-fire. _[authorized-gated] · L · bus:
  produces a signed window-schedule artifact._

---

## How an idea leaves this file

Pick it → write a `TASKS.md` row with acceptance criteria → the owner says go → an agent builds it in
its branch space with hardware-free tests → CI green → lead review → merge. Firing-capable
(`[authorized-gated]`) items need the empty-by-default allowlist in place and the owner's explicit
sign-off before any build starts.

## Sources (representative)

Bjorn (github.com/infinition/Bjorn); jayofelony/pwnagotchi & jayofelony/bettercap; bettercap module
docs (bettercap.org/modules — wifi, ble, hid, graph, gps, api.rest, scripting) + changelog 2.34–
2.41.5; Kismet remote capture & kismetdb (kismetwireless.net/docs); WiGLE CSV (`WigleWifi-1.6`);
Protomaps PMTiles; Sparrow-wifi; KrakenSDR DoA; hashcat WPA/22000 + brain (hashcat.net/wiki);
ZerBea/hcxdumptool 6.3 discussions; read-only rootfs (dzombak, root-ro); RAUC on RPi (Bootlin, FOSDEM
2025); Geekworm X1200 UPS; `vcgencmd get_throttled`; Reticulum (markqvist/Reticulum); Meshtastic
(meshtastic.org, MQTT module); BATMAN-adv (open-mesh.org); Syncthing; TAK/CoT + FreeTAK.
_Note: Mozilla Location Service shut down in 2024, which breaks pwnagotchi's `net-pos` — see G-8._
