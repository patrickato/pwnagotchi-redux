# redux.detect — NOTES (tuning & false positives)

Practical notes for operators and integrators. Thresholds below are constructor
defaults; override per instance for denser or quieter environments.

---

## DeauthFloodDetector

**Flags:** burst of `deauth` + `disassoc` frames in `window_s` (default 5s)
above `threshold` (default 20).

**Tuning**
- Dense enterprise / conference floors: raise `threshold` (30–50) or shorten
  window if you only care about hard floods.
- Home lab: defaults are fine; a single sticky client retrying is usually under
  threshold.

**False positives**
- AP-driven mass deauth on channel change or band steering can look like a
  short flood. Prefer correlating with a stable BSSID concentration in
  `detail` before treating as hostile.
- Some drivers emit spurious disassoc on roam; sparse rates stay quiet.

---

## RogueAPDetector

**Flags:** beacon/probe-resp for a **trusted** SSID where BSSID, channel, or
security is outside the trusted set.

**Tuning**
- Empty trusted list → **always quiet** (same philosophy as empty-by-default
  allowlists elsewhere).
- Leave `channels` / `security` empty frozensets if you only care about BSSID.
- Multi-AP mesh: list every legitimate BSSID for that SSID.

**False positives**
- Guest / IoT radios that rebroadcast the same SSID on a second radio with a
  different BSSID — add both BSSIDs to trusted.
- Extended SSID (same name, different band) with different channel — either
  allow both channels or omit channel constraint.

---

## BeaconSpamDetector

**Flags:** high count of **unique SSIDs** or **unique BSSIDs** among beacons in
`window_s` (default 10s).

**Tuning**
- Urban wardrive: raise `unique_ssid_threshold` / `unique_bssid_threshold`
  (defaults 30 / 40) so normal density does not alert.
- Lab: lower thresholds to demo easily.

**False positives**
- Dense downtown / stadium is naturally high unique-BSSID. Prefer pairing with
  SurveillanceSweep (first-seen rate) rather than unique count alone.
- SoftAP spam from phones can trip SSID uniqueness; usually short-lived.

---

## SurveillanceSweepDetector

**Flags:** many **first-seen** BSSIDs (lifetime set) appearing inside `window_s`
(default 30s), threshold default 25.

**Tuning**
- First power-on in a new area will look like a sweep — expect a one-shot alert
  or raise threshold after a warm-up period (`reset()` after settle).
- Distinguish from beacon spam: this cares about *novelty over time*, not
  uniqueness inside the window alone.

**False positives**
- Walking into a mall / transit hub: legitimate first-seen burst. Operators may
  `reset()` after arriving or use a higher threshold outdoors.

---

## DetectEngine

Runs deauth, rogue, beacon-spam, and sweep on each `feed(frame)`. Detectors stay
independently constructible for unit tests. Wire an `AlertBus` (when present on
your branch) after `feed` if you need cross-detector dedup/escalation.

---

## Karma / captive twin (sibling branch)

When `KarmaCaptiveDetector` is merged:

- **Karma:** one BSSID probe-responds to many distinct SSIDs → PineAP/Karma style.
- **Captive twin:** same SSID from a second BSSID with weaker/open security next
  to a stronger suite.

FP notes: corporate multi-SSID controllers answering probes; dual-band open
captive + secured corp SSID sharing a name (document both or expect alerts).

---

## WPS attack (sibling branch)

When `WPSAttackDetector` is merged: high rate of WPS exchange steps / NACKs
toward one BSSID. Sparse legitimate enrollee M1–M4 stays quiet; PIN hammers and
NACK floods alert.

FP notes: flaky WPS on cheap APs can NACK often — raise `nack_threshold` if
needed.

---

## AlertBus + config (sibling branch)

When present: `DEFAULTS` + `_opt()` in `config.py`; `AlertBus` dedups by
`(kind, bssid, ssid)` inside `dedup_window_s` and escalates severity after
`escalate_after` repeats, appending a glass-box note to `reason`.

---

## Integration checklist

1. Map capture events → `Frame` (lead / engine lane).
2. Optional trusted list for rogue detection.
3. Feed `DetectEngine` (or individual detectors).
4. Optional bus for dedup/escalation.
5. Surface `alert.reason` on the creature / log — do not invent telemetry.
