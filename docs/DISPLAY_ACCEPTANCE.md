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
