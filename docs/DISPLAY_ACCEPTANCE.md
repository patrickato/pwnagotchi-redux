# Redux SPI display & touchscreen acceptance

This is a **separate development SD-card** procedure. Never change the owner's
working Jayofelony Pwnagotchi card. The default Redux image stays Pi OS Lite:
the diagnostics are usable headlessly, and browser/X11 remain opt-in.

## New: native RGB565 diagnostic display (no X11, no Chromium)

The Redux ARM64 Lite image now **contains** `redux-fbui`, a built-in
480×320 telemetry screen driven by the **actual** local live Redux status
API. It shows engine state, capture radio, observed events, completed
capture handoffs, free space and Doctor findings. Separate read-only views
(`--page overview|captures|doctor`) can be chosen on launch. It uses the
small bundled Python Pillow library and renders directly to the selected
16-bit Linux framebuffer; no desktop, Chromium, SDL or X server is needed.

**It does not auto-start, change TFT overlays, initialize SPI, or read
touch input by default.** The image installs an inactive
`redux-fbui.service` and a deliberately invalid example
`/usr/share/redux/fbui.example.env`. It does not install an active config
or enable the unit. The original Jayofelony SD is never modified.

Once the display is enumerated and the current TFT's framebuffer identity is
confirmed (Section 1), an operator may perform this **read-only** check on
the disposable Redux image, substituting the actual `fbN` value:

```sh
sudo redux-fbui --framebuffer /dev/fbN --check
```

The JSON report validates actual `virtual_size = 480,320`, 16 bits per
pixel and reported stride; checks whether a Redux loopback status endpoint
responded; and **always** sets `physical_display_verified=false` and
`touch_verified=false`. A failure refuses to write any pixels.

For the **first visible pixel test only**, after separate operator approval:

```sh
sudo redux-fbui --framebuffer /dev/fbN --once --page overview
```

This writes one real telemetry frame to that selected framebuffer; it does
not control the radio, initialize touch, start a browser or persist config.
If colors are wrong, the operator can try `--order bgr565`; if upside
down on a 480×320 panel, `--rotate 180`. Do not enable a service
until the physically visible result has been verified. A framebuffer that
uses a different depth, stride or orientation must be diagnosed before use.

For a manually supervised refresh after visual qualification:

```sh
sudo redux-fbui --framebuffer /dev/fbN --run --page overview
```

Each refresh reads `http://127.0.0.1:8080/api/status` over loopback
with a 2-second timeout and a 64 KiB maximum response, then paints a
480×320 frame every two seconds. An absent/old health report renders
UNKNOWN/STALE, not a fabricated green status. It shows no raw captures
or credentials. By default the app does not access a touch input node. With the
physically identified ADS7846/XPT2046 event source and operator approval,
the manually started renderer can optionally read **classic single-touch
swipes** to change pages:

```sh
sudo redux-fbui --framebuffer /dev/fbN --run \
    --touch-device /dev/input/eventN --swipe-axis x
```

The `eventN` device and `x` axis must be selected from the actual
`redux-display-diag` readings; neither is guessed. For a physically
rotated controller use `--swipe-axis y` and/or `--swipe-reverse`.
The default raw-axis threshold is 300 units; `--swipe-threshold` can
be selected deliberately after measuring your touch controller's range.
One complete left or right swipe changes between Overview, Captures and
Doctor. Vertical movement on the other axis does not navigate. The reader
is read-only, nonblocking, nofollow, does not EVIOCGRAB or change input
calibration, and resets an incomplete gesture after kernel SYN_DROPPED.

**Touch caveats:** physical accuracy, raw-axis reversal, finger pressure,
tap calibration, swipe latency and long press are not yet hardware
qualified. This first native implementation provides swipe navigation
only; its systemd service intentionally leaves touch **off** until the
operator separately validates input and decides to enable it.

### Offline five-tap calibration preparation

There is now a small companion command,
`python3 -m redux.display.calibration_wizard`
(`redux-touch-calibrate` when installed as a Python entry point).
It can convert a **measured**, existing `redux-display-diag` report into a
candidate `touch-calibration.json` without hand-entering raw coordinates.

Use `redux-display-diag --touch-device /dev/input/eventN --seconds 15`
on the Pi **only after the selected event node and physical panel have
been independently verified and the touch observation is approved**.
During its sampling window, touch and release precisely **five times in
this order: top-left, top-right, bottom-left, bottom-right, center**.
Copy the produced JSON report to the trusted laptop through an approved
read-only workflow. Do not publish it, or the touch coordinates, in the
public repository.

From the source checkout on the laptop, with that JSON already available:

```sh
out="$HOME/redux-touch-candidate-$(date +%Y%m%d-%H%M%S)"
mkdir -m 700 "$out"
python3 -m redux.display.calibration_wizard \
  --report /absolute/path/to/display-report.json \
  --output-dir "$out" --confirm-order
```

Only use `--confirm-order` when the five physical taps were actually
performed in the documented order. The tool requires exactly five complete
raw samples, independently measured axis bounds, no SYN_DROPPED events,
and a consistent 480×320 geometric mapping. It refuses to overwrite
an existing file, uses mode 0600, and **never** automatically enables touch.
The resulting file is compatible with `--calibration-file` described below,
but requires a separately approved deployment and direct physical validation.
The tool's output always marks hardware acceptance as pending.

### Calibrated touchscreen tabs — source-only until a new image is qualified

The native 480×320 dashboard now draws four footer tabs: **Overview**,
**Captures**, and **Doctor**. When a physical five-point calibration has been
observed and explicitly confirmed, classic single-touch taps inside those
footer regions select the visible page. Horizontal gestures still advance or
reverse pages. A diagonal drag, unqualified multi-touch stream, or lost evdev
synchronization cannot be promoted to a tap. Uncalibrated operation retains the
original opt-in swipe-only behavior.

This feature is **not** available on the already-flashed October 15 image
unless a separately approved image update is installed. Never enable a TFT or
input service just because the offline tests passed. Before using it, first
complete the framebuffer/pixel and raw tap acceptance steps below.

Create a **private** absolute JSON file on the writable REDUXCAP partition
containing the five **actually observed** raw X/Y pairs, correctly labeled
by the corresponding physical corner or center of the displayed 480×320
surface. The sample structure is:

```json
{
  "schema": 1,
  "points": [
    {"raw_x": 0, "raw_y": 0, "pixel_x": 0, "pixel_y": 0},
    {"raw_x": 0, "raw_y": 0, "pixel_x": 479, "pixel_y": 0},
    {"raw_x": 0, "raw_y": 0, "pixel_x": 0, "pixel_y": 319},
    {"raw_x": 0, "raw_y": 0, "pixel_x": 479, "pixel_y": 319},
    {"raw_x": 0, "raw_y": 0, "pixel_x": 239.5, "pixel_y": 159.5}
  ]
}
```

**The raw zeros above are placeholders, never measurements.** Substitute
each of them with the actual values from your observed physical touches;
the placeholder file deliberately fails fitting. Do not copy example values
into a supposedly qualified mapping. The input reader caps the file at
8 KiB, rejects leaf symlinks, malformed values, missing target samples,
ambiguous orientation and unacceptable fit residuals.

Only **after** physically verifying the five touches, the actual framebuffer
and the displayed UI orientation, an explicitly approved supervised run can
use the following template (replace both actual device numbers):

```sh
sudo redux-fbui --framebuffer /dev/fbN --run \
  --touch-device /dev/input/eventN \
  --calibration-file /captures/config/touch-calibration.json \
  --confirm-touch-calibration
```

If visible framebuffer pixels are deliberately rotated 180 degrees, also
supply `--rotate 180`. The gesture decoder transforms physical coordinates
back into logical UI coordinates for the four touch targets and swipe direction.
Calibration confirmation is an **operator assertion**, not proof created by
the software. Check all three tabs and both swipe directions in person. The
feature does not change kernel calibration, create event devices, grab input,
enable a service, or modify network state.

### Optional persistent display service after acceptance

Only after physical verification and explicit approval, store the chosen
framebuffer and orientation on the writable REDUXCAP partition so the
read-only OS overlay does not erase them on reboot:

```sh
sudo install -d -m 0700 /captures/config
sudo install -m 0600 /usr/share/redux/fbui.example.env /captures/config/fbui.env
sudoedit /captures/config/fbui.env
# Replace the intentionally invalid fbN and select verified pixel order/rotation.
sudo systemctl enable --now redux-fbui.service
```

The `redux-fbui.service` is installed but not enabled by the image.
It starts only with the manually created config, checks the same framebuffer,
and stops with normal systemd lifecycle controls. Operator ownership,
correct `/captures` mount, display node permissions, verified pixel order
and the actual TFT result remain acceptance requirements.

**Limitations:** The current native renderer requires a 480×320 framebuffer
at 16bpp (RGB565-compatible). It does not claim 320×480 rotation,
automatic touch calibration, physically qualified swipe accuracy, frame timing,
backlight control or cross-board graphics compatibility. Those are follow-on
features, not green gates today.

## Offline view of a saved read-only hardware acceptance report

The `redux-ui-preview` source tool can now render the existing sanitized
`redux-readonly-acceptance-v1` report into the **actual Radios-page pixels**.
For example, from a trusted laptop checkout with Pillow installed, after
capturing an acceptance report locally:

```sh
cd ~/redux-workbench-src
out="$HOME/redux-radio-review-$(date +%Y%m%d-%H%M%S)"
mkdir -m 700 "$out"
python3 -m redux.display.preview \
  --acceptance-json /absolute/path/to/saved-readonly-acceptance.json \
  --output-dir "$out"
```

This produces exactly one private file, `redux-radios.png`. It displays
the previously observed sanitized onboard/USB drivers, modes and kernel
link types. It requires a fresh *at-collection* inventory report with
stable identities and an Ethernet management PASS, and refuses private
unexpected fields, incompatible report schemas and claims of authenticated
ownership or decoded reception in the v1 format.

**This report lacks an authenticated collection timestamp** and is therefore
always rendered as **SAVED INVENTORY / HISTORICAL - NOT LIVE**, even if
the file was generated seconds earlier. No file modification time or
laptop clock is substituted for an authenticated observation timestamp.
It does not connect to the Pi, monitor live state, or change any radio.
The pending independent live-status supplier remains a separate hardware
and runtime integration gate; this screenshot tool is not that supplier.
Do not share captured identifiers or the unreviewed raw report publicly.

## Scrollable Doctor findings on the 480×320 panel

The native Doctor screen is no longer limited to its first three diagnostic
findings. A physically confirmed classic single-touch vertical swipe inside
the Doctor **content viewport** advances one group of three findings, and a
reverse swipe moves back. Footer taps still select Overview, Captures, Doctor
or Radios; horizontal swipes still change pages. The viewport requires an
approximately 60-pixel, directionally dominant vertical gesture that begins
and ends inside the content area. Diagonal touches and footer drags do not
scroll. Display rotation 180° is mapped into logical screen coordinates.

Finding navigation is entirely read-only. The model sorts up to 64 fresh
actionable findings by severity and clamps every offset to the available
entries. Stale or unavailable Doctor telemetry hides historical findings
rather than replaying them as current. Switching pages resets Doctor to the
first finding. After any kernel `SYN_DROPPED` event, the decoder now requires
a new **BTN_TOUCH release after synchronization** before accepting gestures
again; no stale finger coordinates can navigate a page.

For hardware-free inspection of a real Doctor status JSON report, the
preview CLI can render a specific visible window:

```sh
python3 -m redux.display.preview \
  --status-json /absolute/path/to/status.json \
  --output-dir /absolute/existing/private/directory \
  --page doctor --doctor-offset 3
```

The `--doctor-offset` argument accepts 0–61, and the renderer clamps it to
the actual fresh findings. A saved status report whose runtime timestamp is
already stale will show no verified findings, regardless of offset; this
is intentional. Source CI tests are **not** confirmation of the actual
ILI9486 pixels, ADS7846 touch wiring, scrolling ergonomics or performance.
Do not enable the touchscreen service before physical qualification.

## Four-page native navigation and independent radio reporting

Redux now supports four native pages — **Overview, Captures, Doctor and Radios** —
with four 120-pixel footer tabs across the 480×320 display. A physically
confirmed calibrated tap can select any page; horizontal swipes navigate and
wrap in the same order. The uncalibrated swipe-only mode uses that order too.
The offline PNG preview includes `redux-radios.png`.

The Radios page distinguishes the **current selected capture interface**
(observed through the existing, fresh `runtime` status data) from a separate
optional independently sampled **radio inventory**. If no current radio
inventory was supplied, the USB section explicitly reports NOT REPORTED,
even when a USB adapter was previously seen by a separate hardware tool.

The optional, source-only `radio_inventory` status extension is:
```json
{
  "schema": 1,
  "sampled_utc": 0,
  "radios": [
    {"transport": "onboard", "driver": "brcmfmac", "kernel_mode": "monitor",
     "arphrd": 1, "identity_stable": true},
    {"transport": "usb", "driver": "mt76x2u", "kernel_mode": "monitor",
     "arphrd": 803, "identity_stable": true}
  ]
}
```
**The zero timestamp and these radio records are format examples, not a live
reading or hardware attestation.** The current runtime does **not** produce
this extension yet. A future independent source must supply bounded,
same-boot evidence and actual timestamps before this field can be used
operationally. Until then the UI shows the active capture's *real* link-type
metadata from `runtime` and leaves the USB inventory unreported. There is
no automatic sysfs/USB inspection, packet reader, mode change, or process
attachment in the renderer.

Even for a recent record, ARPHRD801/803 and monitor mode are only kernel
metadata. The Radios page always labels decoded RF reception **UNVERIFIED**,
and it never treats an identity-stable flag as verified exclusive ownership.
A stale, duplicated, malformed, or partially changed inventory is discarded
as a whole. This is visual diagnostics, **not an RF qualification or an
authorization mechanism**.

## Measured capture processing on the native TFT

The Captures page now uses the existing `capture_processing` object from the
local Redux status API, which is derived from the **SQLite worker ledger**.
Its three numeric counters are *PREPARED* (cumulative ledger rows with status
`ready`), *FAILED* (cumulative rows with status `error`) and *HASH ROWS*
(the ledger's count of structurally accepted format-22000 records). None of
these counts means a password has been recovered. They are historical ledger
totals, not per-second radio activity.

The lower panel distinguishes ledger availability from the age of the last
worker scan. A fresh runtime reading older than 20 seconds is STALE, and its
ledger counters are suppressed. Even if the runtime is fresh, a worker scan
older than five minutes is independently STALE; old per-pass results are
suppressed. A recent scan shows files examined plus per-pass ready, failed and
deferred outcomes where measured. The worker's real
`paused_low_storage` heartbeat appears as **PAUSED STORAGE**, not as a
successful scan; inconsistent per-pass totals appear as INVALID. Stale,
invalid, and storage-paused worker states are rendered in amber. Unknown
counters are shown as `--`, never fabricated zeros. A successfully opened
ledger without any recorded scan reports NO COMPLETED SCAN.

This is a read-only UI. The numbers originate in the same live-status payload
already used by Redux. The screen never opens captures, the SQLite database,
or the radio. It does not qualify actual native 802.11 reception or the
correctness of captured handshakes. The changes remain **source-only** until a
subsequent signed image is built, flashed and physically accepted.

## Hardware-free exact TFT screenshots (new source tool)

The source tree now provides `python3 -m redux.display.preview` (or
`redux-ui-preview` after installing the optional Python entry point).
It renders the **same `fbui.render()` code** that drives the eventual
480×320 native framebuffer, producing private RGB PNG files. It does not
access /dev/fbN, /dev/input, radio hardware, or the physical Pi.

From the trusted laptop's updated source checkout with Pillow installed:

```sh
cd ~/redux-workbench-src
out="$HOME/redux-ui-preview-$(date +%Y%m%d-%H%M%S)"
mkdir -m 700 "$out"
python3 -m redux.display.preview --output-dir "$out"
```

This produces `redux-overview.png`, `redux-captures.png`, and
`redux-doctor.png` in that directory. **Without a measured status source,
screens clearly show OFFLINE/UNKNOWN rather than fabricated telemetry.**

An existing *local* JSON snapshot shaped like Redux's `/api/status` output
can be displayed with `--status-json /absolute/path/to/status.json`.
The snapshot is size-bounded and its age is checked by the ordinary display
renderer; stale counters never become fresh. The alternative `--live`
only queries the same **127.0.0.1** API endpoint already used by the actual
TFT; if unavailable it fails rather than silently creating fake live frames.
`--page doctor` limits output to one page; `--rotate 180` matches a
physically rotated panel. The command creates 0600 files, refuses to
overwrite existing files or follow an output directory symlink, and
rolls back files created by a failed multi-page export.

These are **real software renders, not verified hardware photographs**.
They do not establish framebuffer enumeration, visible pixels, touch
accuracy, frame timing, monitor reception, or image-boot compatibility.
The preview is source-only until the next qualified image; no services are
enabled or installed by running it in the development checkout.

## 1. Collect the real devices, without modifying them

On a booted development board, run as the local user:

```sh
redux-display-diag > redux-display-inventory.json
cat /proc/fb
find /sys/class/graphics -maxdepth 2 -type f \( -name name -o -name virtual_size \) -print
```

Read the JSON \`framebuffer_candidates\` and \`input_candidates\`: use the
**actual** /dev/fbN and /dev/input/eventN identities. A touch controller
often appears as "ADS7846 Touchscreen" but event numbers are not stable.
Only the selected framebuffer is validated against the expected
480×320 or 320×480 format. The CLI never silently chooses fb0 or fb1.

For the observed framebuffer (substitute your discovered N):

```sh
redux-display-diag --framebuffer /dev/fbN > redux-display-fb.json
```

Run this again **as the local unprivileged user inside the authenticated
X11 session**, once the opt-in manual-X11 profile has been configured.
It will add actual X11 socket, authenticated root geometry and optional
\`xinput\` device inventory. Those checks do **not** prove the Xorg
framebuffer target or physical pixels.

### Read-only SPI controller evidence (source workbench)

The current source workbench also includes a bounded `spi_candidates`
inventory in `redux-display-diag`. For each kernel-enumerated SPI device it
reports the sysfs device name, modalias, Device Tree compatible string (if
available), and bound driver name (if available). These values help separate
a missing SPI/overlay device from a controller that enumerates but still
does **not** expose a compatible `/dev/fbN`.

This evidence is **not** physical display acceptance: SPI enumeration alone
does not verify the framebuffer, actual pixels, backlight or touch. The first
signed field image was built before this inventory extension; it is
**source-only until a future qualified image is deployed**. On that existing
image, inspect the same kernel information with read-only sysfs tools rather
than enabling or guessing a TFT overlay. Only an operator-approved panel
configuration change may create a new framebuffer.

## 2. Sample raw touch presses (explicit opt-in)

After visually identifying the touchscreen's \`/dev/input/eventN\` in the
inventory, run (substitute the real eventN):

```sh
redux-display-diag --framebuffer /dev/fbN --touch-device /dev/input/eventN \
    --seconds 15 > redux-display-touch.json
```

During the interval, tap near the four physical corners and then the
center, releasing the finger between taps. For ADS7846/XPT2046 classic
single-touch input, the JSON shows complete BTN_TOUCH press/release pairs,
raw ABS_X/ABS_Y coordinates, kernel-advertised axis bounds and normalized
0–100% positions where axis bounds are available. Verify directional
ordering: do raw X/Y move as expected across the physical panel, or are
axes swapped/reversed? Repeat after each real rotation/calibration change.

The sampler opens **only the explicit event node**, with O_RDONLY,
O_NONBLOCK and O_NOFOLLOW; it does not EVIOCGRAB, emit input, change
calibration, alter framebuffer state, write files or access wireless radios.
If access is denied, first inspect the input-group/seat ACL configuration.
For a temporary root-only raw event check you may invoke the sampler via
sudo, but sudo does **not** inherit proof of the authenticated X11 session;
run the X11 check separately as the graphical user. Avoid chmod 666,
permanent world-readable event devices, or unsupported universal udev rules.

An empty raw sample is **not** proof of a broken touchscreen: the selected
node, permission, driver, event format or human stimulus may be wrong.
Multi-touch ABS_MT slot streams are intentionally **not decoded** by this
classic ADS7846 sampler; these must be investigated separately. Input
event loss (SYN_DROPPED) is recorded, not converted to a fake tap.
The sampler exits after the configured 1–60 s or bounded number of taps.

## 3. Physical acceptance: manually verify and record

A green preflight or raw touch sample does not mark either hardware gate
complete. On each Pi 4 and Pi 5 image under test, record:

- Board model, kernel, Redux commit/image SHA256, TFT model/overlay, X11
  driver, framebuffer identity, selected input node and orientation.
- Visible test: do the correct 480×320 UI **pixels appear on the TFT**
  rather than HDMI? Are there white/blank screens, rotated rows or clipping?
- Touch: with the actual Redux UI on TFT, tap **all four corners and the
  center**, navigate every bottom tab, scroll and swipe both ways. Verify
  touchscreen coordinates hit the intended onscreen targets, including
  after reboot. Record any raw-to-screen axis flips or X11 transforms.
- Performance: measured cold/warm launch, memory/CPU utilization, input
  latency, Chromium crash/restart recovery, prolonged display stability.
- Operations: a failed/missing TFT must leave SSH/serial/headless Doctor
  usable; the inactive kiosk service must not auto-start on a fresh image.

Retain the JSON reports together with photos/videos of what physically
happened and exact operator observations. Never include secrets or captures
in a public report. **Only a human observing both pixels and corresponding
touch effects can close the hardware acceptance gates.**

## Software validation

```sh
python -m pytest tests/test_display_acceptance.py tests/test_display_kiosk.py tests/test_fbui.py tests/test_touch_nav.py
python -m compileall -q redux/display
```

Both commands run without a Pi. They validate input parsing, failure paths,
mode checks and launch constraints, **not** display electronics or calibration.
