# Hardware validation runbook — the real-Pi pass

Everything in `main` is **sandbox-verified** (merged + `pytest` green, no hardware). Nothing is
`done` until it passes on an actual **Pi 4 and Pi 5 (arm64)**. This runbook is the operator
checklist for that pass: boot the image, run each check, record the real output, and flip the
matching `TASKS.md` row from `done (sandbox)` to `done`. Sandbox-green ≠ done.

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

## 3. Recording results

For each board, append a dated block to `docs/IMAGE_BUILD.md`'s hardware-gate record (image SHA256,
model, command outputs) and update the relevant `TASKS.md` rows:
- all green on **both** boards → flip that row to `done`;
- partial → leave `done (sandbox)` and note exactly what failed and on which board.

Keep it honest: a pass on one board is not a pass on both, and a QEMU/sandbox result never stands in
for a physical one.
