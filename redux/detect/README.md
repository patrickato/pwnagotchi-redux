# redux.detect — defensive detector pack

Pure **detection**. These modules raise glass-box alerts; they never transmit,
deauth, inject, or target anything.

Input is a standalone `Frame` protocol (`frames.py`). Do **not** import
`redux.engine` from here — the lead maps bettercap events → `Frame` at
integration time.

## Quick use

```python
from redux.detect import (
    DetectEngine,
    Frame,
    FrameType,
    TrustedNetwork,
)

eng = DetectEngine(
    trusted=[
        TrustedNetwork(
            ssid="HomeNet",
            bssids=frozenset({"11:22:33:44:55:66"}),
            channels=frozenset({6}),
            security=frozenset({"wpa2-psk"}),
        )
    ]
)

for frame in frames:  # your capture → Frame stream
    for alert in eng.feed(frame):
        print(alert.severity, alert.kind.value, alert.reason)
```

Every `Alert.reason` is a human-readable sentence (glass-box rule).

## Detectors (on this tree)

| Detector | Module | Fires when |
|----------|--------|------------|
| Deauth flood | `deauth_flood.py` | Many deauth/disassoc frames in a short window |
| Rogue AP / evil twin | `rogue_ap.py` | Trusted SSID from unexpected BSSID / channel / security |
| Beacon spam | `beacon_spam.py` | Many unique SSIDs or BSSIDs beaconing in a window |
| Surveillance sweep | `surveillance_sweep.py` | Many *first-seen* BSSIDs in a window (survey burst) |
| Fan-in | `engine.py` | `DetectEngine` runs all of the above on each frame |

Additional detectors may land on sibling `grok/*` branches (Karma/captive twin,
WPS attack, alert bus/config). See `NOTES.md` for signatures and tuning.

## Layout

```
redux/detect/
  frames.py            Frame / FrameType input protocol
  alerts.py            Alert + AlertKind
  deauth_flood.py
  rogue_ap.py
  beacon_spam.py
  surveillance_sweep.py
  engine.py            DetectEngine
  README.md            this file
  NOTES.md             tuning + false positives
tests/test_detect_*.py hardware-free unit tests
```

## Scope

- Authorized / passive by default — this pack never fires RF.
- Real data only in reasons and details.
- Lane-locked: only `redux/detect/**` and `tests/test_detect_*.py`.
