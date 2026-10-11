# Redux SPI display & touchscreen acceptance

This is a **separate development SD-card** procedure. Never change the owner's
working Jayofelony Pwnagotchi card. The default Redux image stays Pi OS Lite:
the diagnostics are usable headlessly, and browser/X11 remain opt-in.

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
python -m pytest tests/test_display_acceptance.py tests/test_display_kiosk.py
python -m compileall -q redux/display
```

Both commands run without a Pi. They validate input parsing, failure paths,
mode checks and launch constraints, **not** display electronics or calibration.
