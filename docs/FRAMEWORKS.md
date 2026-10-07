# Frameworks + purple Range mode (ATT&CK / D3FEND / PTES)

## Why

Degreed pros and classmates both live in frameworks. Tagging every redux action
with its MITRE ATT&CK technique, PTES phase, and a D3FEND countermeasure buys
three things from one mapping: engagement reports that speak the frameworks, a
teaching aid (the box names the technique it runs), and the spine of **purple
Range mode** — attack yourself and grade your own detectors.

## What's built (`redux/frameworks/`)

- **Registry** (`registry.py`) — `TechniqueMap` per offensive action: ATT&CK
  technique IDs, PTES phase, the detector(s) that *should* catch it, and a D3FEND
  countermeasure. `redux range techniques` prints the whole map.
- **Range mode** (`range.py`) — `run_exercise(action, fired_detectors)` grades one
  attack against the detectors that actually fired, and `range_report()` scores a
  set. Verdicts: **DETECTED** (all expected fired), **PARTIAL**, **MISSED** (a real
  gap — attack ran, nothing caught it), **N/A** (passive/network-layer, nothing in
  the wireless suite is meant to catch it). `redux range demo` runs a simulated
  set and names the gaps.
- Pairs naturally with the **purple persona** (`redux persona apply purple`): the
  posture that both attacks and watches.

## Honesty about the mapping

- **ATT&CK IDs are real Enterprise technique IDs**, curated best-fit for Wi-Fi/RF
  (e.g. deauth→T1498, evil-twin→T1557, capture→T1040, portal→T1557+T1056.003).
  Review them against your engagement's threat model.
- **D3FEND's wireless coverage is coarse.** The RF anomaly detectors genuinely are
  Network Traffic Analysis, so they map to `D3-NTA`; off-device/network countermeasures
  use the real D3FEND IDs that fit (`D3-SPP` strong-password-policy for cracking,
  `D3-FEMC` file encryption for loot). It's directional and labelled so — not
  ATT&CK-grade precision.
- **MISS is reported, not hidden.** The whole point of purple is the honest gap: if
  an attack fired and no detector caught it, the report says GAP and the technique
  it slipped past. N/A is explicitly distinct from MISS and never counts against
  coverage.

## Detector-side tags (coordinated, not done here)

This layer references the detector suite **by name** and does not edit it
(`redux/detect/**` is a separate build lane). Adding the ATT&CK/D3FEND tags *into*
the detector definitions themselves — so a live alert carries its D3FEND ID — is a
follow-up to coordinate with the detector lane. The mapping living here first means
Range mode and reports work today, and the inline tags are purely additive later.

## Scope note

Range mode runs real attacks to test detection — all of it scope-gated at the
aiming layer like every other offensive path, and most useful against your own lab
(arm it with `redux scope arm-lab --auto`).
