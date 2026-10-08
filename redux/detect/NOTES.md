# redux.detect — NOTES (tuning & false positives)

Thresholds live in `config.DEFAULTS` and flow through the registry into each
detector when `DetectEngine(options={...})` is used. Override any key without
editing detector source.

```python
from redux.detect import DetectEngine, describe_defaults  # or config.describe_defaults

eng = DetectEngine(options={"deauth_threshold": 40, "pnl_loud_threshold": 12})
```

---

## DEFAULTS reference (DD7)

| Key | Default | Owner |
|-----|---------|-------|
| `dedup_window_s` | 60.0 | AlertBus |
| `escalate_after` | 3 | AlertBus |
| `escalate_to` | critical | AlertBus |
| `deauth_window_s` | 5.0 | deauth_flood |
| `deauth_threshold` | 20 | deauth_flood |
| `beacon_spam_window_s` | 10.0 | beacon_spam |
| `beacon_spam_unique_ssid_threshold` | 30 | beacon_spam |
| `beacon_spam_unique_bssid_threshold` | 40 | beacon_spam |
| `sweep_window_s` | 30.0 | surveillance_sweep |
| `sweep_new_bssid_threshold` | 25 | surveillance_sweep |
| `karma_window_s` | 15.0 | karma |
| `karma_ssid_threshold` | 8 | karma |
| `pineapple_window_s` | 20.0 | pineapple |
| `pineapple_probe_ssid_threshold` | 10 | pineapple |
| `pineapple_beacon_ssid_threshold` | 15 | pineapple |
| `pnl_loud_threshold` | 8 | pnl |
| `wps_window_s` | 30.0 | wps_attack |
| `wps_attempt_threshold` | 12 | wps_attack |
| `wps_nack_threshold` | 6 | wps_attack |
| `handshake_window_s` | 30.0 | handshake |
| `ble_flood_window_s` | 5.0 | ble_flood |
| `ble_flood_unique_addr_threshold` | 20 | ble_flood |

Detectors without tunables (rogue_ap trusted list, ble_tracker, pmf_missing,
hidden_ssid) take no threshold keys from DEFAULTS.

---

## DeauthFloodDetector

**Flags:** burst of `deauth` + `disassoc` in `deauth_window_s` above `deauth_threshold`.

**Tuning:** dense floors → raise threshold 30–50; home lab → defaults fine.

**False positives:** AP mass deauth on channel change; sparse client roam noise.

---

## RogueAPDetector

**Flags:** trusted SSID with unexpected BSSID/channel/security. Empty trusted → quiet.

---

## BeaconSpamDetector / SurveillanceSweepDetector

Urban wardrive: raise unique-SSID/BSSID and first-seen thresholds. First power-on
in a new area looks like a sweep — expect one-shot or `reset()` after settle.

---

## Karma / Pineapple / PNL

Karma & pineapple: one BSSID answering many SSIDs. PNL: one STA probing many SSIDs.
Corporate multi-SSID controllers can FP — raise thresholds.

---

## WPS / Handshake / BLE flood / PMF

WPS: PIN hammers vs flaky NACK APs — raise `wps_nack_threshold` if needed.
Handshake: observational capture alerts only.
BLE flood: Flipper-style unique-addr bursts.
PMF missing: info advisory, not an attack.

---

## Integration checklist

1. Map capture events → `Frame` (lead / engine lane).
2. Optional trusted list for rogue detection.
3. `DetectEngine(options=overrides)` for thresholds.
4. Optional `AlertBus` for dedup/escalation.
5. Surface `alert.reason` — do not invent telemetry.
