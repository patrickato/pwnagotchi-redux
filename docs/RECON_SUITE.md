# Beast Recon Suite — design notes & proposals

_Proposed 2026-10-06. Status: notes only, nothing built yet. For patrickato/test-plugins (idea backlog) → graduate approved pieces to plugins-wip._

**Scope (non-negotiable, same as the rest of the project):** every capture/crack piece operates only on networks you own or have written authorization to test. The detector/DF pieces are passive receive-only.

**Data rule (your call, baked in):** every map/plot is driven by real captured data. A sample dataset may ship only as a clearly-labeled placeholder shown until real data loads — never presented as real, never mixed into real output. (The wardrive map already works this way; same engine, same rule.)

---

## The spine: one shared engine, many views

Instead of six plugins each reinventing storage and plotting, build **two shared pieces once**, then everything is a thin view on top:

- **SpatialDB** — one SQLite store of "sightings": `(kind, id, ssid/name, channel/freq, rssi, lat, lon, bearing, first_seen, last_seen, meta_json)`. `kind` = ap | client | capture | attack_event | tracker. Everything below writes rows here.
- **BeastPlot** — the offline plot/map engine from the wardrive map (true-distance projection, color/size encoding, live redraw, filters). Every "map" below is just BeastPlot pointed at a filtered query of SpatialDB.

Why this matters: Capture Map, RSSI/DF Map, Attacker Locator and Tracker Locator stop being four map projects and become four queries against one engine. Half the code, one consistent UI, and they cross-reference each other for free.

---

## 1. Capture → Crack pipeline (3 tools, one flow)

Flow: **pwnagotchi captures** → **Triage scores it** → **Crack Pipeline offloads it** → **results land on the Map + CrackHouse**. Each stage writes to SpatialDB so the map always reflects truth.

### 1a. CaptureTriageNG
| | |
| --- | --- |
| **What** | Reads each `.pcap`/`.22000` as it lands, scores it: is a full EAPOL 4-way or a usable PMKID actually present? crackable / partial / junk. Dedupes by BSSID, keeps the best, tags each with its GPS fix. |
| **Already exists?** | Partly, as scattered CLI: `hcxpcapngtool` reports what's in a capture, `aircrack-ng` tells you if a handshake is present, `wpa-sec` uploads them. None of it is integrated, on-device, scored, or deduped into a queue. |
| **Why ours wins** | Runs on the Pi against the capture the moment it's grabbed, turns "I have 400 pcaps" into "37 are actually crackable, here they are ranked, the rest are junk." Feeds the pipeline and the display. Nothing wastes GPU later. |
| **Pipeline role** | The filter. Only crackable captures move downstream. |
| **Bad side / misuse** | Triage is an efficiency multiplier for cracking — pointed at handshakes captured from networks you don't own, it makes an attacker faster by telling them which stolen captures are worth the effort. The tool only *reads* pcaps, but it sharpens the offensive workflow. The scope rule (authorized captures only) is what keeps it a lab tool. |

### 1b. CrackPipelineNG
| | |
| --- | --- |
| **What** | A job queue that hands triaged captures to **hashcat** running on the Pi5 or your PC, tracks status (queued/running/cracked/failed), and writes results back to CrackHouse + the map. A wrapper/orchestrator around hashcat — not a new cracker. |
| **Already exists?** | `wifite` chains capture+crack but isn't pwnagotchi-integrated or offload-aware; `hashcat` + shell scripts do the work but with no queue, no status, no loop-back. Pwnagotchi has no native crack-offload at all. |
| **Why ours wins** | The Pi4 can't crack; this ships the work to the Pi5/PC where the GPU is, tracks every network's state, and closes the loop so the map shows cracked vs. not in real time. The whole "capture→crack" chain you like, automated end to end. |
| **Pipeline role** | The engine room. |
| **Scope** | Only captures from your own / authorized networks. This is the piece closest to the edge, so the authorization gate is the whole point. |
| **Bad side / misuse** | This is a turnkey WiFi-breaking assembly line if aimed at other people's networks — capture, auto-triage, auto-crack, mapped results, no manual steps. The automation *is* the force multiplier. Legitimate because it operates on captures you're authorized to hold; illegitimate the moment it isn't, and nothing technical stops that but you. |

### 1c. CaptureMapNG
| | |
| --- | --- |
| **What** | BeastPlot view of where every capture happened, colored by crack status (cracked / crackable / junk), sized by signal, filterable by crypto/SSID. Real `survey.json`/capture data only. |
| **Already exists?** | **Yes** — the `webgpsmap` pwnagotchi plugin plots handshakes on a map. Honest prior art. |
| **Why ours wins** | webgpsmap needs online map tiles (dead weight at your half-mile-from-anyone lab with no signal); ours is the fully-offline BeastPlot, no tiles, works on the laptop with zero connectivity. Plus it's wired to crack status and the shared DB, so it's a live picture of the pipeline, not a static pin dump. |
| **Bad side / misuse** | A geolocated map of networks + which ones you've cracked is, in the wrong hands, a target map: "here are the networks I can get into, and exactly where they are." Cross-referenced with the crack results it's a shopping list with coordinates. |

---

## 2. RSSI / Direction-Finding Map (DFMapNG)

| | |
| --- | --- |
| **What** | Estimates where a transmitter physically sits from RSSI sampled as you move + your GPS track (gradient/trilateration-ish), and draws a "it's that way, ~N meters" bearing on BeastPlot. Real samples only. |
| **Already exists?** | Partly — Kismet has signal/location data and some tooling; commercial WIDS do bearings. Nothing pwnagotchi-native, nothing that plots it live on your kit. |
| **Why ours wins** | On-device, integrated with the same DB, turns "I hear it" into a direction on screen using hardware you already carry. Pairs with the Attacker Locator below. |
| **Bad side / misuse** | Direction-finding doesn't care what it's locating. The same math that points you at a rogue AP points you at *a person's phone or a specific device* — it's the core of physically hunting down where someone is. That's the surveillance/stalking edge, and it's inherent to DF, not something bolted on. |

---

## 3. AttackSourceLocatorNG — the "who / what / where", live

This is the blue-team piece reshaped the way you actually want it: not "a deauth is happening" but **"deauth flood from `aa:bb:cc:dd:ee:ff`, bearing NE, ~40 m, right here on the radar, now."**

| | |
| --- | --- |
| **What** | Passively watches your airspace for attack patterns — deauth/disassoc floods, auth/assoc floods, beacon/SSID spam, evil-twin/karma responders, EAPOL/handshake hammering — identifies the **source MAC**, classifies **what** it's doing, and localizes **where** via RSSI/DF, painted live on BeastPlot as it happens. |
| **Already exists?** | Partly — Kismet raises alerts, `nzyme` is an open WiFi-defense/forensics project that detects deauth/evil-twin and has some bearing work, commercial WIPS do this at price. None of it is a live, visual, direction-on-a-radar box running on your own pwnagotchi kit. |
| **Why ours wins** | Real-time radar instead of a log line, source + classification + bearing fused in one view, on gear you already own, offline. It's the exact mirror of WifiJammer — same frames, pointed inward as a sensor that catches someone doing it *to you*. |
| **Bad side / misuse** | Two edges. First, a precise classifier for "what a deauth/flood/spam looks like" is, read backwards, a spec for *generating* those attacks cleanly — detection knowledge and attack knowledge are the same knowledge. Second, the locator half will happily pinpoint any transmitter, so it's a people-finder as much as an attacker-finder, and it can be used to hunt down and neutralize someone else's sensors/defenses. |

---

## 4. TrackerLocatorNG (finishes BluetoothReconNG's tracker work)

| | |
| --- | --- |
| **What** | Flags BLE trackers that keep reappearing near you — AirTag, Tile, SmartTag, generic find-my beacons — alerts, logs to the DB, and (with DF) points you at the one that's following. |
| **Already exists?** | **Yes** — Apple's "Tracker Detect" app, the open-source **AirGuard** (Android), and the Apple/Google unwanted-tracking standard all do detection. Honest prior art. |
| **Why ours wins** | Cross-vendor in one place, always-on on the pwnagotchi instead of a phone app you have to open, logs over time, correlates with your WiFi/GPS data, and can *locate* the tracker, not just warn that one exists. |
| **Bad side / misuse** | The signature DB that lets you *detect* trackers also lets you *find and inventory* them — including locating a tracker someone placed legitimately (anti-theft on an asset) in order to pull it. Turned on BLE generally, the same "recurring device near me over time" logic is people-tracking. And publishing exact detection signatures teaches a tracker maker how to advertise in a way that dodges detection. |

---

## Repo placement & how to add

- **Where:** this is an idea-backlog/proposal doc, same shape as `OFFENSIVE_BLUETOOTH_IDEAS_2026-09-29.md`. Drop it at the top level of **patrickato/test-plugins** and link it from `README.md`. When you approve a piece, it graduates to **plugins-wip** as its own suite (SpatialDB + BeastPlot land first as shared deps).
- **Build order that respects the spine:** SpatialDB + BeastPlot → CaptureTriageNG → CrackPipelineNG → CaptureMapNG → DFMapNG → AttackSourceLocatorNG → TrackerLocatorNG.
- **Pushing:** direct git push from this session is blocked by the egress proxy — per our setup it goes as a git bundle run by a `.bat` from Explorer on your laptop. I can prep that bundle, or you can drop this file into the repo and commit it yourself with the usual attribution trailer. Say which and I'll set it up.
