# Image build — arm64 Pi 4 / Pi 5

`./build.sh` creates a new Pi OS Lite Bookworm arm64 image using pinned official
pi-gen stages 0–2 and `stage-redux`. It includes bettercap, patched BCM43455c0
nexmon firmware, nexutil, the bootstrap `redux.service`, and the standalone
passive `redux-live.service` with an independent capture-ingest timer.
No pwnagotchi image or Python runtime is reused. The original Bettercap unit
is masked so only the Redux-owned supervisor can start the capture engine.
These services are source/CI integrated; actual boot and capture remain
physical acceptance gates.

## Linux host

Use root on Debian/Ubuntu with network access, at least 30 GB free, and an ext4
workspace without spaces. WSL2 can build under ARM emulation; use its persistent
Linux filesystem, not NTFS or `/tmp`. Host prerequisites:

```sh
sudo apt-get update
sudo apt-get install git quilt parted coreutils qemu-user-static debootstrap \
  libarchive-tools zerofree zip dosfstools e2fsprogs libcap2-bin grep rsync xz-utils curl xxd \
  file kmod bc gpg pigz arch-test binfmt-support ca-certificates
sudo update-binfmts --enable qemu-aarch64
arch-test arm64
sudo ./build.sh
```

The `REDUXCAP` partition defaults to 512 MiB for compatibility. For sustained
passive capture on a larger SD card, explicitly choose an image capacity that
fits both the card and the build host, for example:

```sh
sudo REDUX_CAPTURES_MIB=8192 REDUX_BUILD_DIR="$PWD/build/image-8g" ./build.sh
```

The supported build setting is 64–262144 MiB, subject to the underlying image
size and filesystem capacity. It sizes the third ext4 partition at image-build
time; it does **not** auto-expand a flashed card, erase existing capture
data, or affect a running installation. Use a disposable SD card and allow
sufficient scratch space for the uncompressed image. The original default
is maintained unless this variable is supplied.

The wrapper refuses existing build directories. Choose a fresh path for another
run with `sudo REDUX_BUILD_DIR="$PWD/build/image-next" ./build.sh`. Filesystem
mounts from a failed pi-gen run must be unmounted before removing any build tree.
`--prepare-only` stages sources without root or image creation; it is not a build.

Output: `build/image/pi-gen/deploy/*.img.xz` with a matching
`.img.xz.sha256` checksum and `.img.xz.release.json` provenance manifest.
The wrapper streams and verifies the complete xz payload, checks all
three MBR partitions for valid non-overlapping extents, verifies the capture
partition's ext4 superblock magic and `REDUXCAP` filesystem label without
mounting the image, and records the exact Redux, pi-gen, and Nexmon source
revisions. It **explicitly records `hardware_tested: false`**; file-integrity
verification is not Pi boot verification. A missing, truncated, undersized, or
ambiguous image causes release publication to fail.
Only the redux stage exports an image; there is no desktop stage.

For local login, country, locale or timezone provisioning, pass a private shell
config using `REDUX_IMAGE_CONFIG=/absolute/path/image.local.conf`. Keep it outside
the repository, because shared ignore rules are lead-owned. First-boot user
provisioning remains enabled, SSH is off, and no shared password ships. The
placeholder account is `field`; use Pi Imager or a local console to provision it.
Blank-but-set `WPA_COUNTRY` makes upstream configuration fail, so it is unset by
default. The wrapper fixes the release and stage list; arm64 is fixed by pi-gen.

## Decisions and provenance

- **arm64 on both boards:** the pinned arm64 pi-gen Bookworm revision selects
  Debian arm64 userspace and Pi OS kernel/firmware packages. Pi 4 uses the v8
  kernel; Pi 5 uses 2712. Both kernel images and headers are installed, and the
  build compiles nexmon for each installed target. `arm_64bit=1` preserves the
  kernel/userspace pair. No armhf/Pi Zero target is supported.
- **Sources pinned:** `image/sources.sh` records exact pi-gen and maintained
  jayofelony/nexmon revisions. The maintained nexmon tree includes 6.12/6.18
  drivers and supports the distro compiler on aarch64. Only its firmware,
  driver and tools are reused. Its license is retained with the image.
- **Real target headers:** host `uname -r` is never used to choose modules.
  Missing headers/source or an unsupported kernel aborts the build. A tested
  adapter reads actual cfg80211/SDIO headers, accepts the known added net_device
  argument and renamed SDIO identifier, and fails on unknown APIs. Compatibility
  reasons and final module checksums are recorded in `/usr/share/redux/`.
- **Firmware priority:** patched firmware is in `/lib/firmware/updates/brcm/`
  with Pi 4 and Pi 5 board aliases. Packaged stock firmware is retained. pi-gen
  generates the final boot initramfs during export, after modules are installed.
  The actual firmware/chip/driver combination on each board is a hardware gate;
  compilation alone does not establish monitor/injection support.
- **Kernel held:** installed image packages are held so APT cannot replace a
  kernel without its nexmon module. Rebuild the pair to upgrade it.
- **Lean runtime:** compilers, headers and build sources are removed. The boot
  package uses the standard library and needs no pip/network download. Package
  versions, source revisions, kernels and firmware/module hashes are recorded.
  Live APT repositories and filesystem timestamps mean this is source-pinned,
  not a bit-for-bit reproducible image.

Pinned upstream references: [pi-gen arm64 Bookworm](https://github.com/RPi-Distro/pi-gen/tree/816f458a9931e216ecf8969e13b4d0ca39947d1f),
[maintained nexmon](https://github.com/jayofelony/nexmon/tree/1654e1857766df92086dbfbed5ffd288efc9bd8c).

## Optional 480×320 graphical kiosk profile (manual X11)

The base image remains **Pi OS Lite**, not a full desktop. It always ships
`/usr/local/bin/redux-kiosk`, a Python standard-library preflight/launcher,
but deliberately does **not** install Chromium/X11 or auto-start a browser.
This avoids turning every passive capture appliance into a heavy desktop,
and prevents a guessed framebuffer from blanking a working HDMI console.

To stage the **opt-in** browser stack, build a fresh image with:

```sh
sudo REDUX_KIOSK_PROFILE=manual-x11 REDUX_BUILD_DIR="$PWD/build/image-kiosk" ./build.sh
```

`manual-x11` adds Debian Bookworm ARM64 Chromium, Xorg/fbdev,
xinit, Xauthority and libinput packages. The profile is recorded in
`/usr/share/redux/kiosk-profile`; the exact installed package versions
remain in the image's `/usr/share/redux/packages.tsv`. Chromium is
substantial (the browser package alone occupies hundreds of MiB, before
dependencies), so account for the larger root filesystem and RAM use.
An unsupported profile fails the image build. No new systemd service
is enabled, and Redux's existing capture supervisor remains independent.

**On the separate development SD card, after confirming the actual TFT:**

1. Identify the GPIO SPI framebuffer from
   `cat /proc/fb`, `cat /sys/class/graphics/fb*/name`, and
   `cat /sys/class/graphics/fb*/virtual_size`. Do **not** assume fb1.
   Confirm ILI9486 versus the actual panel board; the existing
   `REDUX_TFT_PROFILE=mpi3501` is **not** a universal Waveshare/XPT2046
   driver and does not include proven touch calibration.
2. Confirm `redux-live.service` is running and
   `curl --fail http://127.0.0.1:8080/api/status` returns a real
   Doctor JSON report. The browser does not start the radio.
3. Start an actual X11 session **locally as a non-root console user**.
   Configure the Xorg `fbdev` driver for the verified framebuffer;
   for example, the device section must use your observed
   `Option "fbdev" "/dev/fbN"`, not a copied `fb1` value.
   Test Xorg, the framebuffer orientation and the XPT2046 input
   mapping separately. Session setup and permissions vary by image
   and must be validated before enabling any boot automation.
4. Inside that X11 session with a writable `XDG_RUNTIME_DIR`, use
   `redux-kiosk --check --framebuffer /dev/fbN` (replace fbN with
   the actual device). This prints a structured JSON report and exits
   nonzero if the framebuffer, 480×320/320×480 mode, browser, unprivileged
   graphical environment or local dashboard cannot be verified.
5. Only after a passing check, run
   `redux-kiosk --launch --framebuffer /dev/fbN`.
   It replaces itself with sandboxed Chromium in kiosk mode at
   **http://127.0.0.1:8080/**, with a private browser profile under
   the session's temporary runtime directory. It does not enable
   `--no-sandbox`, download web assets, open a remote address or
   start a web server.

The preflight intentionally reports `touch_verified: false` and
`physical_display_verified: false` even when software checks pass.
The active Xorg seat, framebuffer mapping, SPI performance, touch
coordinates/rotation, screen visibility, and reboot behavior are
separate hardware acceptance gates. In particular, Xorg or Chromium
may fail to use a legacy SPI fbdev node on a given target kernel.
Do not call this profile plug-and-play or mark the physical TFT tested
until the actual device proves it.

## Verification

```sh
python -m compileall redux
pytest
for f in $(git ls-files '*.sh'); do bash -n "$f"; done
GITHUB_HEAD_REF=codex/image GITHUB_BASE_REF=main python .github/scripts/lane_guard.py
```

Hardware-free tests exercise real preparation/staging with local source fixtures,
service shutdown, target selection for both v8/2712, rejection of armhf/unknown
kernels, installed-package filtering and compatibility adapters.
The earlier armhf image is historical evidence only and does not validate this
arm64 build. Each PR records the current build result and physical gates honestly.
External nexmon objects are cleaned between the v8 and 2712 builds, and the
resulting module's actual vermagic must match that target before installation.
An initial arm64 export exposed stale 2712 objects in the v8 path; that artifact
is rejected, even though its archive and checksum manifests were internally valid.

**Hardware gate — Pi 4 AND Pi 5 (no boards available in this session):**
Flash the arm64 artifact, provision locally, then record the image SHA256, model
and these actual outputs on each board, including after a second boot:

```sh
cat /proc/device-tree/model; echo
uname -a; getconf LONG_BIT
systemctl is-enabled redux.service
systemctl is-active redux.service
journalctl -b -u redux.service --no-pager
command -v bettercap nexutil
modinfo -n brcmfmac
cat /usr/share/redux/nexmon-kernels.txt
sha256sum -c /usr/share/redux/nexmon-firmware.sha256
sha256sum -c /usr/share/redux/nexmon-driver.sha256
systemctl is-enabled bettercap.service  # masked, expected nonzero exit
```

Confirm arm64 userspace, the board's matching kernel and `updates/brcmfmac.ko`.
No physical success or sub-15-second boot claim is inferred from QEMU tests.


## Overlay root and captures (Codex backlog 2)

`overlayroot` from Debian is configured as `tmpfs:recurse=0`: the lower root is
read-only and the upper root is RAM. Recursion is disabled so `/captures` remains
writable. The export patch adds a third, 512 MiB by default (operator-resizable at build
time) ext4 partition labeled REDUXCAP
before loop attachment, formats it, and creates `/captures/redux` owned by the
service account with mode 0700. The private live engine later uses this directory; the
unprivileged watchdog/bootstrap stores its own checkpoint in `/captures/boot`.
Unsupported upstream layout changes fail closed.
The root and captures sizes are separate; captures can be recovered independently.

Boot firmware mounts read-only. Swap is masked, the journal is volatile and capped
at 16 MiB, and redux requires the captures mount before starting. Persistent service state belongs under `/captures` (`/captures/boot` for the
unprivileged bootstrap and `/captures/redux` for live engine data), never the
ephemeral root. Captures uses ext4's journal;
yanking power may still lose unsynced captures or damage an SD controller. This
protects root writes, not every possible storage failure.

Provision operator credentials in a private image config before building overlay
images (`FIRST_USER_PASS` and `DISABLE_FIRST_BOOT_USER_RENAME=1`). First-boot changes
made only in the RAM overlay disappear; an unprovisioned image is a service-only
bootstrap, not a persistent interactive enrollment. For explicit maintenance,
boot with `overlayroot=disabled` on the command line and remount boot writable;
restore the overlay and read-only boot settings before field use. Never put an OTA
signing private key or shared login password in the image source.

**Hardware gates on both boards:** confirm `findmnt / /captures /boot/firmware`,
`findmnt /media/root-ro`, and `lsblk -f`; root must be an overlay with a read-only
lower, boot must be ro, and REDUXCAP must be rw. Write a temporary root file and a
capture file, sync, reboot: only the capture survives. Exercise power-loss recovery
on a spare card and confirm redux fails to start if the captures filesystem cannot
mount. These results are pending, not inferred from the layout tests.

## Boot budget (Codex backlog 3)

The boot policy masks network wait units, unattended APT jobs, EEPROM auto-update,
cellular probing, mDNS activation and man-db timers. NetworkManager itself stays
available for the orchestrator. Every mask has a reason in
`/usr/share/redux/boot-policy.txt`.

Initramfs uses `MODULES=list` and the union of actual modules/builtins from both
installed kernels. It retains ext4, overlay and SD support plus available USB and
NVMe storage drivers; dependencies are added by initramfs-tools. It never derives
storage drivers from the build host's devices. Missing fundamental support aborts
trimming. This is a disk-boot image; network-root boot is not a supported profile.

Run `sudo redux-boot-report` after a cold boot on each model and paste its complete
output in the PR. It records the real model, boot ID, `systemd-analyze time`,
`systemd-analyze blame`, redux's critical chain and its start timestamp. No Pi is
available here, so no blame output or sub-15s result is claimed. The sub-15s target,
SD/USB/NVMe boot coverage and measured regression comparison remain hardware gates.

## Watchdog and restart records (Codex backlog 4)

The image enables `dtparam=watchdog=on` and retains the actual kernel's
`bcm2835_wdt` support in the initramfs. PID 1 owns `/dev/watchdog0` through
`RuntimeWatchdogSec=15s` and `RebootWatchdogSec=15s`; redux stays unprivileged.
The bootstrap uses `Type=notify` and `WatchdogSec=15s`, sends readiness only after
its startup checkpoint is durable, and sends heartbeats from its supervised loop.
A stalled loop therefore cannot keep itself alive through a separate timer thread.
This checks the bootstrap loop; it does not claim health coverage for RF workers
that the lead has not connected to this entrypoint.

`/captures/boot/boot.json` records real UTC startup/shutdown times and whether the
previous shutdown was committed. Replacement syncs the temporary file and parent
directory. Missing or corrupt records have explicit reasons; unclean restart
resumes the passive bootstrap and never replays radio actions. Storage failure
prevents readiness, allowing systemd to restart the process rather than report a
healthy service with lost state.

**Hardware gates on Pi 4 and Pi 5:** record `cat /sys/class/watchdog/watchdog0/identity`,
`systemctl show --property=RuntimeWatchdogUSec --property=RebootWatchdogUSec`,
`systemctl show redux.service --property=WatchdogUSec --property=MainPID`, and the
bootstrap journal. Verify watchdog0 is the board's hardware watchdog, not a
software watchdog or a different attached device. On a spare card, stop the
bootstrap process with SIGSTOP and confirm systemd times it out and restarts it;
the next checkpoint must describe an unclean shutdown. Test a complete system
hang/reset with an operator present and confirm root/captures recovery. A normal
shutdown must commit a clean checkpoint. No reset results are inferred from tests.

EEPROM/kernel handover watchdog settings are not enabled automatically: a timer
running before PID 1 could reset a board during its unmeasured first boot. Verify
boot duration and the board-specific handover before enabling that additional
protection. See the upstream [systemd watchdog configuration](https://github.com/systemd/systemd/blob/main/man/systemd-system.conf.xml)
and [Raspberry Pi boot watchdog documentation](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/config_txt/boot.adoc).

## UPS and TFT battery state (Codex backlog 5)

`redux-power.service` installs a monitor with hardware selection disabled by
default. It reads Linux Battery/UPS supplies through their real `capacity`,
`status` and optional `voltage_now` attributes. The Geekworm adapter reads the
manufacturer's gauge at I2C address 0x36 and does not write gauge/charging
registers. Its AC input is a configured GPIO line: high means external power,
low means discharge. Without that input, capacity is still displayed but shutdown
is inhibited. No voltage curve is used to invent state-of-charge or charging.
Protocol references: [Geekworm X120x gauge](https://github.com/geekworm-com/x120x/blob/main/bat.py),
[AC input](https://github.com/geekworm-com/x120x/blob/main/pld.py), and
[Linux power-supply units](https://www.kernel.org/doc/html/latest/power/power_supply_class.html).

Pass `REDUX_POWER_CONFIG=/absolute/private/power.json` when building. A sysfs
profile sets `enabled: true`, `backend: "sysfs"` and `supply` to the actual
`/sys/class/power_supply/<name>` path. A Geekworm profile sets `backend: "geekworm"`,
`bus: 1`, the verified `gpio_chip` path and `ac_line` offset. X1200's manufacturer
reference uses GPIO6; select the correct chip by its label on the actual Pi 5,
never assume its numeric gpiochip index. X728 revisions need their own verified
AC line; a guessed line can produce a false power-loss reading. Use `gpioinfo`
during the hardware gate and keep other GPIO consumers off the selected input.

Default policy requires capacity at/below 10% while discharging continuously for
30 seconds, sampled every 5 seconds. Unknown/invalid/absent/charging state or
capacity recovery resets confirmation. A sample gap longer than twice the configured
`poll_seconds` also restarts confirmation: time without readings does not establish
continuous discharge. One delayed/missed polling cycle is tolerated. The decision
reports the actual gap and its limit, and withholds shutdown until a fresh
confirmation window completes. The threshold and delay are configurable;
validate the real battery's margin before field deployment. On confirmation the
service commits its measured reason on captures, syncs and requests systemd
poweroff, allowing redux's shutdown checkpoint to complete. It does not operate
the HAT's power-cut/charging pins. Such a cut needs model-specific board support;
Linux shutdown alone may leave the HAT powered.

Set `REDUX_TFT_PROFILE=mpi3501` to enable the distro's fbtft ILI9486 SPI overlay
at 16 MHz, reset GPIO25, data/command GPIO24, rotated 90 degrees. These follow
the [MPI3501 pinout](https://www.lcdwiki.com/3.5inch_RPi_Display). Set `framebuffer`
in power.json only after identifying that display's actual device. The renderer
validates 480x320/320x480 truecolor, stride, offsets and memory bounds and reserves
the top-left 160x48 pixels for a monochrome battery icon, measured percent/voltage
and status. Missing data displays Unknown. It leaves the remaining screen to the
lead's UI; the lead must reserve this strip or consume
`/run/redux-power/battery.json` (atomic, read-only to consumers) instead. Touch
calibration and complete creature-screen integration are outside this OS item.

**Hardware gates:** identify the UPS revision, bus/AC GPIO and TFT device on each
board; compare readings to the manufacturer's tool; verify external power is
never reported as discharge; verify the selected display initializes and shows
real/unknown readings without conflicting GPIO ownership. With a spare card,
observe the actual low threshold, cancellation when AC returns, graceful shutdown
and captures/checkpoint recovery. These peripherals are unavailable in this
session; the shipped default does not claim they are present.

## Signed A/B OTA (Codex backlog 6)

Build the A/B profile by passing `REDUX_OTA_CERT=/absolute/public-keyring.pem`.
The file must contain a parseable public X.509 certificate; private keys are
rejected. No production certificate or update URL is invented or committed.
Without a certificate the ordinary overlay image is built and RAUC is masked.
The A/B profile uses GPT: selector FAT p1 (32 MiB), Boot A/B p2/p3 (512 MiB each),
Root A/B p4/p5 (4096 MiB each), captures p6 (512 MiB). Use at least a 16 GB card.
`REDUX_ROOT_SLOT_MIB` and `REDUX_CAPTURE_MIB` in private image config can increase
capacity; both root slots remain equal and the exporter refuses an undersized slot.
Do not run the stock first-boot partition resizer on this topology; it is removed
from both command lines.

The slot-aware systemd generator discovers the physical disk from the real root
PARTUUID and validates all six GPT labels/unique UUIDs before creating mounts and
RAUC config in `/run/redux-ota`. It follows the disk across SD, USB or NVMe names;
it does not hardcode `/dev/mmcblk0`. Both roots share identical content. Separate
boot command lines point to their paired roots and declare `rauc.slot=A/B`.
Boot, selector and lower root mount read-only; captures alone remains writable
during field operation. RAUC temporarily writes the inactive boot/root slots;
health commit briefly remounts the selector writable and syncs its replacement.
Selector FAT power-loss behavior still needs physical testing; filesystem rename
tests do not establish firmware-level atomicity.

`autoboot.txt` uses the [Raspberry Pi tryboot A/B flow](https://www.raspberrypi.com/documentation/computers/config_txt.html#tryboot_a_b).
The [RAUC custom backend](https://rauc.readthedocs.io/en/v1.8/integration.html#custom)
stages an inactive candidate without changing the committed default. Signed
verity bundles must contain both boot and rootfs; a post-install hook adjusts the
candidate's command line to this device's actual root UUID. Captures and selector
are excluded from bundles. State and reasons are synced to `/captures/rauc`.
The candidate commits only after 60 real seconds of stable redux PID, active
hardware watchdog, correct firmware partition, overlay/read-only lower root and
correct writable captures. Failed validation or the independent 120-second trial
deadline requests a normal reboot to the old slot. Interrupted/abandoned trials
are recorded honestly; no radio work is replayed.

Before enabling unattended updates, provision and physically test EEPROM
`BOOT_WATCHDOG_TIMEOUT=15..300` seconds on each board; `redux-ota install` and
RAUC's pre-install handler refuse updates without it. The A/B image supplies
`kernel_watchdog_timeout=60` plus PID 1's runtime watchdog to cover handover.
Codex does not rewrite EEPROM automatically. Verify the firmware version supports
GPT, tryboot and these watchdog properties, especially early Pi 4 revisions.
If firmware/kernel never reaches userspace, automatic fallback depends on that
verified watchdog reset. Keep a recovery card available while testing.

On the release host install `rauc squashfs-tools`, then sign an **uncompressed**
exported image with an external private key:

```sh
sudo bash image/make-bundle.sh image.img release.raucb signing-cert.pem signing-key.pem VERSION
```

The helper mounts A read-only, preserves root ownership/ACLs/xattrs, signs a verity
bundle, and verifies the result. Keep private keys on the release host. On a
provisioned board run `sudo redux-ota install /path/release.raucb` or a real HTTPS
bundle URL. RAUC verifies signatures/compatibility before changing inactive slots;
successful staging requests `reboot "0 tryboot"`. No periodic downloads are
configured. A trusted clock and network/storage support for RAUC's streaming path
are additional deployment gates; local signed bundles need no update server.

**Hardware gates on both models:** normal A and B boot; a signed A→B→A update;
signature/compatibility rejection; candidate redux failure; candidate kernel/root
failure with watchdog reset; interrupted install; selector commit power loss on
a spare card; preserved captures and correct read-only mounts after rollback.
Record firmware versions, actual RAUC status/journals and boot IDs. These checks
remain unverified without boards, and no automatic rollback success is claimed.

## Packs image infrastructure (Codex backlog 7)

The image installs `packs list`, `packs install kali-tools --keyring PUBLIC.gpg`
and `packs remove kali-tools`. `--dry-run` reports the concrete arm64 suite,
packages and reason without installing. No pack is preinstalled. The initial
Kali-tools pack contains tcpdump/tshark for packet inspection, starts no services
and executes no capture/transmission tool. This is OS tools infrastructure, not
the Beast SDK's theme/plugin loader or an attack launcher.

Each pack has a separate root at `/captures/packs/<name>/rootfs`, its own signed
APT sources/database and a real installed-package version manifest. It cannot
upgrade the base Pi OS, its kernel or nexmon. Kali rolling is kept in that root;
never add Kali sources to the Pi OS base APT configuration. Supply the current
verified Kali archive **public keyring** independently (no committed trust key),
then choose the official HTTPS mirror or an explicitly trusted mirror. The helper
requires writable ext4 captures and root, retains a failed install for inspection,
and refuses symlink destinations or removal of mounted pack trees. Packs consume
captures space; increase the partition size before building if needed. Installation
is not automatic on boot and needs network plus sufficient free space.

Release hosts can run `bash image/packs/repository.sh REVIEWED_DEBS NEW_REPO_DIR
[SIGNING_KEY_ID]` after installing `apt-utils`. It creates conventional
`pool/main`, `dists/redux/main/binary-arm64/Packages{,.gz}` and Release hash metadata
from the actual arm64/all .debs. Optional external GPG signing produces InRelease
and Release.gpg. Unsigned output is explicitly staging-only; no source uses
`trusted=yes` or bypasses APT verification. Publishing an endpoint/trust key and
adding reviewed packages are operator/lead release actions; no server is invented.

**Deployment gates:** install/remove the signed Kali pack on Pi 4 and Pi 5,
inspect its actual packages.tsv, confirm no daemon/capture starts and base
kernel/driver checksums stay identical; reboot to confirm captures persistence.
Test disk-full and interrupted installation and verify explicit removal refuses
any active mounts. Repo metadata and pure helper guards are hardware-free checks;
network installation/real Pi storage behavior are labeled deployment gates.

## Flash and verify a release

On Linux, obtain the image SHA256 through a trusted release channel. Unmount all
partitions of the removable SD-card reader, inspect `lsblk`, then run:

```sh
bash image/flash.sh release.img.xz --sha256 TRUSTED_SHA256 --device /dev/sdb --dry-run
sudo bash image/flash.sh release.img.xz --sha256 TRUSTED_SHA256 --device /dev/sdb --confirm-device /dev/sdb
```

The second command erases the selected card. The helper checks the compressed
release hash and decompresses the entire image before writing, requires a writable
removable whole disk with enough space and no mounted descendants, and prints its
actual model/capacity and reason. It rechecks disk identity and opens the target
without creating or truncating files, refuses a final symlink, and verifies the
opened descriptor is still the selected block device before writing. Linux
[`O_EXCL` block-device semantics](https://man7.org/linux/man-pages/man2/open.2.html)
refuse an in-use disk. The helper holds that exclusive descriptor through writing,
cache invalidation and SHA256 read-back, so verification does not reopen a possibly
replaced device path. It handles short writes and closes the descriptor on failure.
Fixed disks and partition targets are refused. Some readers report
themselves as fixed disks; use a reader that exposes removable media.

Tests use explicitly synthetic image streams and block-inventory fixtures.
**Hardware gate:** SD-card write/read-back, safe removal and subsequent physical
Pi 4/5 boot remain unverified without the card reader and boards. A verified byte
copy establishes storage integrity, not firmware compatibility or successful boot.


## Hardware-free image CI

Install `shellcheck` and the repository's Python test dependencies on a Linux host,
then run `bash image/ci.sh`. This compiles image/boot Python, checks every owned
shell script with Bash syntax and ShellCheck warning/error severity, and runs the
image/nexmon/boot tests. Sourced pi-gen helpers are checked as Bash. The immutable
source manifest has a documented unused-variable exception because its consumers
read those variables after sourcing it.

Argument tests exercise the real `build.sh` parser with prerequisite tripwires:
help, unknown arguments, and excess arguments must return before source fetching,
workspace creation or any build prerequisite. Valid `--prepare-only` remains
covered by the pinned-source preparation fixture. These tests are part of the
existing repository pytest CI; no workflow changes are needed. Missing ShellCheck
fails explicitly rather than silently skipping lint. The lead may additionally
call `bash image/ci.sh` from its workflow.

This CI builds no image and requires no root, ARM emulator, card or radio. Full
arm64 image export and physical Pi 4/5 boot/storage/watchdog/rollback checks remain
separate build and hardware gates; a green dry run does not establish boot success.
