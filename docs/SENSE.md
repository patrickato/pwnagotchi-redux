# redux/sense — Wi-Fi CSI sensing (the radio as a motion sensor)

## The idea

We kept **nexmon**. nexmon has a CSI extension (`nexmon_csi`) that makes the Pi's
own Wi-Fi radio report **Channel State Information** — per-subcarrier amplitude/
phase for the frames it hears. A still room produces flat CSI; a moving body
perturbs the multipath and the CSI's *temporal variance* jumps. That variance is a
motion signal. No camera, no microphone, no extra hardware — the radio we already
have becomes a presence sensor.

Because we have **nexmon + a sighting store + the detector bus + LoRa**, this
unlocks:

- **Home / dropbox sentinel** — deploy it, it watches for *motion in the space*
  (not just new APs) and can alert over the mesh lane when something moves while
  you're away.
- **Occupancy as a recon signal** — "is this space occupied right now" is a real,
  passive bit for a scoped engagement.
- **Anti-tamper** — a planted node can tell when someone walks up on it or handles
  it.

## What's built (sandbox-verified)

Pure sensing math, fully tested without a radio:

- `CsiFrame` — per-subcarrier amplitudes at a timestamp (`from_iq()` builds it from
  I/Q pairs).
- `MotionSensor` — sliding-window motion indicator (mean per-subcarrier temporal
  variance) against a **measured quiet baseline**, with a z-score threshold.
- `OccupancyTracker` — hysteresis occupied/vacant so a brief still moment doesn't
  flip an occupied room; vacancy only after a sustained motion-free stretch.
- `SenseEngine` — one `observe(frame) -> dict` + glass-box `status()`.
- `estimate_rate_hz()` — **experimental** breathing/periodicity estimator
  (autocorrelation). Real in the literature; treated as experimental here.
- CLI: `redux sense demo` (synthetic quiet→motion→quiet, proves the pipeline) and
  `redux sense replay <frames.json> [--calibrate N]`.
- Augur: `enable_sense()`, `observe_csi(frame)`, `sense_status()`, and a
  `sense` block in `status()` when enabled.

## The honesty contract (same as the rest of redux)

- **No baseline → UNKNOWN, never "no motion."** An uncalibrated sensor does not get
  to claim a room is quiet. "Haven't measured the baseline" ≠ "nothing there."
- **Everything is relative to a measured baseline**, and each reading exposes the
  raw value, the baseline, and the z-score — not a bare boolean.
- **Nothing invented** — malformed/empty frames are skipped, never zero-filled.
- UNKNOWN readings never move the occupancy state either way.

## What still needs a real Pi (needs-hardware)

- **`parse_nexmon_csi()` is fenced and unverified.** It models the documented
  nexmon_csi UDP layout (fixed header + little-endian int16 I/Q pairs), but the
  header length and subcarrier count are firmware/chip/bandwidth specific
  (bcm43455c0 on the Pi differs from other chips). It MUST be validated against a
  real capture on the target firmware. The sensing math does **not** depend on it —
  it consumes `CsiFrame`s from any source — so this is the only piece a Pi has to
  confirm.
- **Live UDP source.** A real `CsiSource` reading nexmon_csi's socket on-device.
- **Calibration & thresholds in the real RF environment**, and the breathing
  estimator against real breathing.
- Firmware: the CSI-enabled nexmon build on the jayofelony 64-bit image.

## Privacy / scope note

CSI presence-sensing is genuinely surveillance-capable. It fits our "scope, not a
cage" model — passive to run, and aimed at a space you own or are authorized to
monitor — and the glass-box reasoning is deliberately loudest here.
