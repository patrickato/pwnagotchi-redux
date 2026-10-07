# Sentinel mode — deploy-and-watch guardian (blue / purple)

## Why

The biggest synthesis in the platform: six systems we already built become a
*product* with one persona flip. Point the detector suite + CSI presence-sensing
at a space, arm it, walk away — it tells you if someone floods the air with
deauth, stands up an evil twin, drops a tracker, or just **moves in the room while
you're gone** — and routes the alert out over LoRa. Your attack platform is a
defense platform.

## What's built (`redux/sentinel/`)

- `Sentinel` — consumes the alerts the detector suite already emits (duck-typed,
  never reaches into that lane) and CSI readings from the sense engine; classifies
  severity (INFO/WARN/CRITICAL), de-duplicates a chattering signature within a
  window, and dispatches via a `notifier`.
- `CollectingNotifier` (log/test) and `CallableNotifier(fn)` — wrap any sender,
  e.g. a LoRa mesh `send`, as the notifier. Honest absence: no notifier → it still
  records to history.
- Augur: `enable_sentinel(notifier, armed, min_severity)`; `pump()` routes
  detector alerts to it and `observe_csi()` routes CSI motion; `sentinel_status()`.
- CLI: `redux sentinel demo` (a simulated armed stream showing dispatch, de-dup,
  and armed-vs-home suppression). Pairs with `redux persona apply blue`.

## The honesty rules

- **Armed vs home.** CSI motion only alerts when *armed* (you've left). At home it
  is suppressed, not fired — no crying wolf at your own footsteps.
- **UNKNOWN never alerts.** A warming/uncalibrated CSI reading is ignored, never
  treated as motion.
- **De-dup is glass-box.** A repeating signature is collapsed within the window and
  the re-fire carries its repeat count (`x3`), instead of spamming identical
  alerts. Suppressed events are counted, not dropped silently.

## Field wiring (needs-hardware)

The notifier is injected, so the LoRa send is a one-line `CallableNotifier` over
the mesh lane on-device; the tracker feed (`observe_tracker`) expects the
BLE-tracker detection signal. Both are honest stand-ins in the sandbox and get
real feeds on the Pi. Everything above is sandbox-verified.
