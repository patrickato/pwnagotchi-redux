# Raw 802.11 capture tap (`redux/captap/`) — the P0 gap, closed (producer side)

## Why

bettercap's REST event stream is high-level and does **not** expose raw 802.11
management frames. That single gap is what kept three features dark: deauth-flood
and surveillance-sweep detection, and the fingerprint Dex's PNL/IE
re-identification. One tap feeds all three.

## What's built (sandbox-verified)

- **Pure 802.11 parser** (`dot11.py`): `parse_dot11(bytes, radiotap=…)` →
  `Dot11Frame` for deauth/disassoc (src/dst/bssid/reason) and probe-request (the
  directed SSID = a PNL entry, the ordered IE tags, and an **IE fingerprint hash
  with the SSID excluded** so it identifies the device regardless of which network
  it probes). It also parses **beacons / probe-responses** (an AP announcing itself
  → SSID + channel from the DS Parameter Set) for the passive survey. Radiotap
  header length is honored. Truncated IEs stop cleanly — never invented. A tiny
  frame builder documents the wire layout and drives tests.
- **CaptureTap** (`tap.py`): routes probe-requests into the fingerprint Dex (real
  PNL + IE → cross-MAC re-identification) and deauth/disassoc into normalized
  `DeauthEvent`s.
- **Passive AP inventory** (`CaptureTap.access_points()`): beacons/probe-responses
  accumulate into an `AccessPoint` list (BSSID, channel, SSID, how many we heard,
  first/last seen) — what's in earshot, listen-only.
- **Augur.ingest_frames(frames)** + `redux captap demo`: synthetic frames show a
  phone re-identified across two randomized MACs (shared PNL + IE → one device,
  strength 1.00) alongside a staged deauth burst.

## The honesty line

- The parser never fabricates — a truncated/short/non-management frame yields a
  clean None or stops, never a guessed field.
- Re-identification still obeys the fingerprint guards: linking needs a
  MAC-independent signal (PNL or IE), OUI alone never links, strength is reported.
- The live monitor source (`live_source`, an AF_PACKET raw socket on a monitor
  interface) is **needs-hardware** and honest about absence — it raises a clear
  error rather than yielding fake frames. The parser/tap take bytes from any source,
  so only that one adapter depends on a radio.

## Detector wiring (closed)

The detector lane has landed, so the loop is closed in software: `detect_bridge.to_frame`
maps a `Dot11Frame` → the detector lane's `Frame` (deauth→DEAUTH, disassoc→DISASSOC,
probe-req→PROBE_REQ), `CaptureTap.detect_frames()` yields them in capture order, and
`Augur.ingest_frames()` runs them through the same `DetectEngine` as every other
source. A real deauth burst fires `deauth_flood` end-to-end (raw bytes → parse →
bridge → detector → voiced alert) — verified in tests and `redux captap demo`. The
`DeauthEvent` list is still exposed for consumers that want the normalized events.

## Passive survey (`redux survey`)

A read-only situational-awareness sweep: listen on a monitor interface (hops
1/6/11 by default, or `--channel N`) and inventory the APs in earshot from their
own beacons — BSSID, channel, SSID, beacon count — plus any detector alerts raised
passively during the sweep. `--demo` runs it on synthetic beacons (no radio). It
never transmits, targets, arms, or populates anything downstream; a test
(`test_beacons_are_passive_only…`) locks that in — beacons never become deauth
events or detector-fire traffic.

The web dashboard renders the same kind of inventory as an **access points · seen
in range** panel, sourced from the Cache (the wifi sightings the device has
actually recorded): CH / BSSID / signal / SSID, strongest first.

## What's left

- **Live capture on hardware** — monitor mode + raw-socket privileges on the Pi
  (runbook §2b.1/2b.6). The parser, bridge, detector wiring, and fingerprint re-id
  are all sandbox-done; only the AF_PACKET monitor source is a Pi step. The live
  survey sweep (`redux survey --iface …`) shares that same monitor-mode Pi step.
