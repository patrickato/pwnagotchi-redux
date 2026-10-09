# Hardware validation runbook — the real-Pi pass

Everything in `main` is **sandbox-verified** (merged + `pytest` green, no hardware). Nothing is
`done` until it passes on an actual **Pi 4 and Pi 5 (arm64)**. This runbook is the operator
checklist for that pass: boot the image, run each check, record the real output, and flip the
matching `TASKS.md` row from `done (sandbox)` to `done`. Sandbox-green ≠ done.

## Recorded results

### Pi 4 Model B — 2026-10-08 · cleared detection/survey/framework lane (`redux hwtest`)

First real-hardware pass of the cleared lane, via
`redux hwtest --iface wlan1mon --seconds 30` on a Pi 4 Model B Rev 1.5. Listen-only;
nothing transmitted.

- **Software battery — all green on-device:** detect engine (13 detectors), config
  template+validate, vault seal/open roundtrip, captap pipeline (synthetic cross-MAC
  re-id + `deauth_flood` fired), sighting store. Board read as Pi 4 Model B.
- **Radios enumerated:** `wlan0mon, wlan0, wlan1mon`. Capture engine: `bettercap` present.
- **Live passive capture (the real test):** `wlan1mon` parsed **703 real frames** → **2 APs**
  (ch6) and **3 device identities**, `0 re-id`, `alerts=none` — correct for a short, quiet,
  attack-free capture. Confirms the AF_PACKET monitor source → radiotap/dot11 parse → survey +
  fingerprint chain works against a real radio and real airspace.
- **SKIP (expected):** CSI — needs the `nexmon_csi` build.

**Still pending (not yet validated):** live-attack detection against a real stimulus
(deauth-flood / surveillance-sweep — a short burst on your own AP), a real handshake capture,
the fox-hunt RSSI walk, TFT / UPS / OTA, and the full pass on Pi 5.

## Scope / authorization (read first)

Every capture/handshake check below is run **only against your own AP and your own client
device, on hardware you own, in your own lab.** Nothing here targets a network or device you are
not authorized to test. The deauth/firing path stays gated by the **empty-by-default
authorized-target allowlist** — do not populate it for this runbook; the handshake check uses a
client you control reconnecting on its own (or a manual reconnect), not a deauth against a
third party. If a step would transmit at anything outside your own allowlisted lab gear, stop.

## 0. Image gate (prerequisite)

Do the image-level gate first — it is already specified and lives in the image lane:
see **`docs/IMAGE_BUILD.md` → "Hardware gate — Pi 4 AND Pi 5"** (model, arm64 userspace, matching
kernel + `brcmfmac.ko`, `redux.service` enabled/active, `bettercap`/`nexutil` present, firmware
checksums, bettercap.service masked). Record image SHA256 + board model. Only proceed once that
passes on both boards.

## 1. Functional gates (Phase-1 acceptance, per board)

Run on **each** board (Pi 4 and Pi 5), onboard radio first, then with one known adapter (e.g. an
MT7612U Alfa). Record the actual output of each command, not a paraphrase.

### 1.2 — Radio capability probe → `Radio` records
```sh
redux status                      # capture_iface + intent render from real state
python -c "from redux.radio import probe; import json; print(json.dumps([r.__dict__ for r in probe()], default=str, indent=2))"
```
**Pass:** onboard + any plugged adapter are enumerated with correct bands, `monitor`, and
`inject` likelihood matching the real chipset (not guessed). A degraded/limited radio is reported
as such, not hidden.

### 1.3 — udev hotplug → roles re-apply, no manual steps
```sh
journalctl -b -u redux.service -f   # watch while you act
# plug the adapter, wait, then unplug it
```
**Pass:** plugging promotes the stronger capture radio and repoints capture live; unplugging falls
back to onboard. No manual `airmon-ng`/`iw` steps. Each transition is logged with a reason.

### 1.4 — bettercap captures a real handshake, zero pwnagotchi in path
```sh
redux run                           # drive the supervisor/bettercap loop
# on YOUR OWN AP: have YOUR OWN client reconnect; confirm the capture lands
ls -l /etc/pwnagotchi/handshakes/ 2>/dev/null; ls -l <configured capture dir>
pgrep -a pwnagotchi && echo "FAIL: pwnagotchi in path" || echo "ok: no pwnagotchi process"
```
**Pass:** a handshake file for your own AP is written, and **no pwnagotchi code/process** is in the
path (redux drives bettercap directly). Record the capture path and the bettercap version.

### 1.5 — intent switch re-arranges radios + repoints bettercap, shown on the TFT
```sh
redux run                           # then issue an intent change (hunt ↔ recon ↔ survey)
```
**Pass:** switching intent reassigns radio roles and repoints the capture interface, and the
creature/assignment is reflected on the 3.5" TFT. Confirm the TFT actually renders (not just logs).

### 1.1 / 1.6 — the hands-off demo
**Pass:** flash → boot → plug the Alfa → it auto-arranges and starts capturing on your own AP, with
no manual steps. Record a short clip. This is the headline; it is only `done` when reproducible on
a real Pi 4 **and** Pi 5.

## 2. Platform surfaces (quick on-device confirmations)

```sh
redux web                           # dashboard serves on the least-exposed scope; note the exact URL
# open the URL on the device/LAN; confirm it shows REAL status (intent, capture_iface, sightings,
# recommendation reason) — no decorative/fake telemetry
redux packs                         # list/enable/disable a pack on-device
```
Spatial (if a GPS is attached): confirm sightings persist to the SQLite store and carry real
lat/lon (and are `None`, not fabricated, when no fix is available).

## 2b. Subsystem on-hardware validation (the 13 built this cycle)

Everything below is **sandbox-verified only**; each has a real-hardware gate. Run on
your own gear in your own lab; any firing step is gated by the central Scope armed to
**your own** targets (`redux scope arm-lab --auto`), never a third party.

### 2b.1 — CSI sensing (`redux/sense/`, nexmon_csi)
```sh
# firmware: the CSI-enabled nexmon build must be flashed; confirm the CSI UDP stream
redux sense replay <real_csi_capture.json> --calibrate 200   # over a REAL capture
```
**Pass:** `parse_nexmon_csi()` is validated against a **real** nexmon_csi capture on the
target firmware (header length + subcarrier count correct for bcm43455c0); a quiet-room
calibration yields a stable baseline; walking through the room reads `motion`/`occupied`
and an empty room returns to `still`/`vacant`. **Still UNKNOWN until calibrated — never a
default "still".** Breathing estimate is experimental: record, don't gate on it.

### 2b.2 — capture engines (`redux/crack/capture.py`, AngryOxide + bettercap)
```sh
which angryoxide; redux capture --iface wlan1 --persona red plan   # engine selection
redux doctor                        # capture-engine finding must be OK (not ACTION)
```
**Pass:** with AngryOxide installed it is the selected engine and a **validated** handshake
(22000) is written for your own AP and cracks with hashcat; with it removed, bettercap is the
honest fallback and Doctor/boot-POST report the engine truthfully (no fake green).

### 2b.3 — WPA-Enterprise EAP (`redux/eap/`, hostapd-mana)
```sh
redux eap --persona red plan --ssid <YOUR_OWN_ENTERPRISE_SSID> --authorized
# stand up the rogue AP on your OWN enterprise test SSID; auth YOUR OWN client
redux eap parse <hostapd-wpe.log>   # -> hashcat -m 5500 lines
```
**Pass:** your own client's MSCHAPv2 challenge/response is captured and the `-m 5500` line
cracks against a known test password. **Own enterprise test network only.**

### 2b.4 — mesh scope-sync over LoRa (`redux/mesh/`)
```sh
# 2+ nodes with LoRa radios + the same swarm key; arm on node A
redux scope arm-lab --bssid <own> ; # observe it propagate to node B's scope
```
**Pass:** an arm on one node converges to the others over the real LoRa link; a delta signed
with the wrong key is **rejected**; LoRa duty-cycle stays legal (deltas are small; periodic
`emit_full` resync). Confirm the transport is wired (`enable_scope_sync`) to the real Scope.

### 2b.5 — RSSI fox-hunt (`redux/hunt/`)
```sh
redux hunt demo        # sanity; then feed live RSSI for your own AP while walking
```
**Pass:** walking toward your own AP reads `warmer` and the band tightens; walking away reads
`colder`; with a real GPS, the weighted-centroid estimate lands near the AP within its stated
error radius. **No fake meters — a coarse band only.**

### 2b.6 — fingerprint re-identification (`redux/dex/fingerprint.py` + `redux/captap/`)
```sh
redux captap demo        # synthetic sanity (fires re-id + a deauth_flood)
# then LIVE, adapter in monitor mode (on YOUR OWN gear only):
redux captap live --iface wlan1mon --seconds 30
```
**Pass:** with live monitor capture feeding captap, your own phone is re-identified as ONE device
across its MAC randomizations (shared PNL/IE); two unrelated devices never collapse. The tap
producer + linker are sandbox-done; the remaining hardware dependency is just monitor-mode capture.

### 2b.7 — Sentinel end-to-end (`redux/sentinel/`)
```sh
redux persona apply blue ; redux sentinel …   # arm, walk away
```
**Pass:** a detector firing on your own gear and CSI motion while **armed** both dispatch a
glass-box alert over the real notifier (LoRa/log); at-home (disarmed) motion is suppressed;
a chattering signature de-dups with a repeat count.

### 2b.8 — autonomous operator (`redux/operator/`)
Wire the real executors (capture→AngryOxide, crack, netrecon) and run a campaign against your
own armed AP. **Pass:** the chain advances only while steps succeed, an unarmed target is
blocked at capture, and the generated engagement report is **CLEAN** (no out-of-scope action).

### 2b.9 — Governor under real load (`redux/core/governor.py`)
```sh
vcgencmd measure_temp; vcgencmd get_throttled    # feed real readings via observe_resources
```
**Pass:** real temp/throttle/battery readings shift the mode (FULL→…→SURVIVAL), the loop cadence
stretches, and recovery is held ~20 s. An unavailable reading never escalates or fakes a 0.

### 2b.10 — boot-POST / Doctor can't fake green (`redux/core/post.py`)
```sh
redux post --tft    # on the panel at boot
```
**Pass:** POST reads READY only when the **critical** capture radio passes AND a capture engine
is actually present; with no engine it is honestly DEGRADED; a probe that can't measure reads
UNKNOWN, never a green.

### 2b.11 — Scope persistence across reboot
**Pass:** `redux scope add`/`arm-lab`, reboot, `redux scope list` still shows the armed targets;
an expired entry has lapsed on its own. The firing gate refuses when empty.

### 2b.12 — raw-frame detectors via the live tap (`redux/captap/` → `redux/detect/`)
The deauth-flood / surveillance-sweep chain is now **wired**: captap parses raw 802.11 and feeds
the same `DetectEngine` as every other source (sandbox-verified end-to-end — `redux captap demo`
fires `deauth_flood`). The remaining step is the live monitor source:
```sh
redux captap live --iface wlan1mon --seconds 30   # mid-run, trigger a short deauth burst
# on YOUR OWN AP against YOUR OWN client (e.g. aireplay-ng --deauth)
```
**Pass:** a real deauth burst on your own gear raises a glass-box `deauth_flood` alert (voiced /
notified over the real notifier); a quiet airspace stays silent (no false positives). The CSA and
WPA3-downgrade detectors (from the detector lane) likewise fire only on their real signatures.

### 2b.13 — operator first-run, at-rest encryption, dashboard auth
```sh
export AUGUR_PASSPHRASE='…'; redux init --dir /etc/redux --node-id <name>
redux config check --config /etc/redux/config.toml
redux web --bind-scope lan --config /etc/redux/config.toml      # note the printed access token
```
**Pass:** `init` writes `config.toml` + a **sealed** swarm keystore (the file begins `AUGURv1`, not
plaintext); `config check` is clean once the USER-INPUT fields are filled; the LAN dashboard refuses
without the token (401) and serves with it; `redux vault seal`/`open` round-trips a loot file; the
airspace panel shows real channel/RSSI from the Cache. With the crypto extra absent, the keystore
is honestly skipped (never written in plaintext).

## 3. Recording results

For each board, append a dated block to `docs/IMAGE_BUILD.md`'s hardware-gate record (image SHA256,
model, command outputs) and update the relevant `TASKS.md` rows:
- all green on **both** boards → flip that row to `done`;
- partial → leave `done (sandbox)` and note exactly what failed and on which board.

Keep it honest: a pass on one board is not a pass on both, and a QEMU/sandbox result never stands in
for a physical one.
