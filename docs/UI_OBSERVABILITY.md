# Shared UI observability

The local status API adds `ui` version 1 without changing existing producer
fields. `redux/display/ui_state.py` builds this pure, bounded presentation model
from the supplied snapshot. It performs no filesystem scans, telemetry sampling,
ledger reads, radio operations or control actions. Both the HTTP handler and native
System renderer use it. Legacy clients can continue reading their existing fields.

The System workspace exposes CPU temperature, utilization, RAM, frequency,
firmware flags, power/battery availability, Governor provenance, Doctor unknowns
and remediation, capture storage, path evidence, radio roles/readiness and worker
state. Radio and processing interpretation reuses their existing display models.
A configured capture path is explicitly **existence unverified**. Ledger readiness
and kernel framing never certify received frames, handshakes or physical readiness.

Each evidence row has a bounded label/value/source, freshness state and optional
measurement timestamp. Runtime heartbeat (20 seconds), sensors (10 seconds) and
visual observations (producer's bounded validity window) have separate clocks.
Future, nonfinite and missing timestamps cannot establish freshness. Historical
sensor numbers remain visible with **stale** labels. Missing battery or power
remains **unavailable**, including when firmware flags are zero or Governor is full.
Doctor coverage gaps remain unknown. A valid sensor timestamp does not imply all
sensor fields are present. Browser connection failure marks the entire evidence
view stale, independently of the last server-side classification.

## Native 480 × 320 navigation

The six current workspaces are Overview, Captures, Doctor, Radios, Trends and
System. The page registry has no fixed maximum. Horizontal swipes cycle through
all pages; the five visible footer tabs follow the selection. For example, select
Trends to reveal System, then tap System. The header shows position and swipe
arrows. Selected tabs share geometry with the actual gesture hit testing.
Targets are 96 × 44 pixels. Content moves above the larger footer without
resampling fonts. System vertical swipes scroll three evidence rows at a time;
Doctor retains its existing bounded findings scrolling. Tap a System evidence
row to read its value, remediation/provenance and measurement timestamp; tap
the content again to return. Selection and scrolling only change the display. CLI `--page system`
selects System without touch. Input remains opt-in using an explicitly selected
node and previously verified calibration. A disconnected input device closes once
and leaves status rendering operational; reconnecting input requires restarting
the UI. No input device is discovered, grabbed or reconfigured.

The native header also shows actual CPU temperature, utilization and RAM with
sensor freshness. Text uses measured glyph widths and ellipses to stay within
columns, with expanded System details for longer evidence.

The native process retains at most one previous framebuffer for write suppression,
a bounded trend window, and 24 font instances. Identical frames are not written.
Polling remains bounded at two seconds; a disconnected API renders offline.
This UI is a separate read-only client, so its failures cannot stop capture.
Native output is still restricted to the existing verified 480 × 320 RGB565
framebuffer contract; arbitrary geometries are not silently enabled.

## Browser and offline acceptance

The web System route participates in existing keyboard (Left/Right, Home/End),
URL hash and swipe navigation. Narrow displays use horizontally scrollable 44px
navigation targets and wrapping evidence. Evidence summaries expand to show
provenance and timestamps. Updates preserve expanded details when measurements
change; unchanged rows avoid DOM replacement. Inputs are text nodes, never HTML,
and rendering caps the evidence view at 80 rows. Existing themes and rich/plain
presentation remain available.

Run software acceptance locally:

```sh
python3 -m pytest -q tests/test_ui_state.py tests/test_fbui.py \
  tests/test_fbui_gesture_integration.py tests/test_gesture_nav.py \
  tests/test_web_refresh.py tests/test_web_navigation.py tests/test_display_preview.py
mkdir /tmp/redux-ui-preview
python3 -m redux.display.preview --output-dir /tmp/redux-ui-preview
```

The preview uses the actual Pillow framebuffer renderer. With no input snapshot it
shows offline/unknown data. Use an existing sanitized snapshot with `--status-json`
and `--page system --system-offset 6` to inspect deeper evidence. Exports refuse to
overwrite existing files. Node.js runs the embedded JavaScript against the headless
DOM contract; these tests do not assert browser pixel layout or physical operation.

Physical acceptance remains open: panel readability and viewing angles, controller
mapping, calibration, reliable taps/swipes, display rotation, framebuffer channel
order and device disconnect behavior on Pi 4/5. Browser CSS contracts are checked
statically at the supported responsive breakpoints; actual browser/device layout
and assistive technology acceptance remain operator gates. No preview or synthetic
input test constitutes physical screen, touch or RF certification.
