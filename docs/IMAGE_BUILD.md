# Task 1.1 — Pi 4 image stub

This builds a **new Raspberry Pi OS Lite Bookworm image** with pi-gen stages
0–2 plus our `stage-redux`. It includes bettercap, the BCM43455c0 nexmon
7.45.206 firmware, a driver compiled for the installed Pi 4 kernel, nexutil,
and an enabled `redux.service`. It contains no pwnagotchi runtime.

The service is an image bootstrap, not the live supervisor: it waits for
shutdown and states in the journal that tasks 1.2–1.5 are pending. It performs
no radio operations, starts no bettercap process, and reports no invented
telemetry. The stock bettercap unit is masked so installation cannot start
an unscoped engine session. Radio intent, target authorization and engine
lifecycle belong to later queue items.

## Build on Linux

Use a Debian/Ubuntu Linux host with root access, an ext4 workspace without
spaces, network access and at least 20 GB free. Windows/Git Bash and an NTFS
workspace are unsupported. On WSL2, clone into its Linux filesystem and make
sure loop devices and binfmt_misc are usable; a native Linux host is preferred.
Use a persistent Linux directory for the clone (for example, `/var/tmp/`
in WSL), since `/tmp/` can be cleared when the distribution restarts.

Install the host prerequisites (derived from pinned pi-gen's `depends`):

```sh
sudo apt-get update
sudo apt-get install git quilt parted coreutils qemu-user-static debootstrap \
  zerofree zip dosfstools e2fsprogs libcap2-bin grep rsync xz-utils curl xxd \
  file kmod bc gpg pigz arch-test binfmt-support ca-certificates
```

On a non-ARM host, verify QEMU's ARM interpreter is registered:

```sh
sudo modprobe binfmt_misc
sudo update-binfmts --enable qemu-arm
cat /proc/sys/fs/binfmt_misc/qemu-arm
```

Then, from the repo root:

```sh
sudo ./build.sh
```

The compressed image and build logs are in `build/image/pi-gen/deploy/`.
Only the final redux stage exports an image (`*-pwnagotchi-redux-pi4.img.xz`);
there is no desktop stage. pi-gen also emits its package/image metadata.

`./build.sh --prepare-only` fetches sources and stages the build without root,
APT, chroot, mounts or image creation. It is **not** a successful image build.
Build directories are never deleted or reused by this wrapper. For another
build, select a fresh path:

```sh
sudo REDUX_BUILD_DIR="$PWD/build/image-next" ./build.sh
```

For locale, timezone, regulatory country or local provisioning, create a
private shell config and pass its absolute path through `REDUX_IMAGE_CONFIG`.
For example:

```sh
sudo REDUX_IMAGE_CONFIG="$PWD/image.local.conf" ./build.sh
```

The temporary build login is `field` (Debian already owns the `operator`
group). Upstream pi-gen's first-boot user provisioning remains enabled: use a local
console or Raspberry Pi Imager to configure your own operator credentials.
SSH is disabled by default and no password is shipped. A private config can
use pi-gen's `FIRST_USER_PASS`, `DISABLE_FIRST_BOOT_USER_RENAME` and SSH options
for a lab build; never commit credentials. The image's service starts at
multi-user boot independently of interactive login.

## Decisions and provenance

- **Bookworm armhf, Pi 4 only:** a conservative supported base with distro
  `gcc-arm-none-eabi` for the maintained nexmon build. `arm_64bit=0` and the v7l kernel keep the
  driver/userspace/kernel combination consistent. This is not a Pi 5 or
  64-bit image. Moving to Trixie/arm64 needs a separate validated build.
- **Pinned sources:** [`image/sources.sh`](../image/sources.sh) identifies
  exact revisions. pi-gen is from Raspberry Pi's official distribution
  builder. The nexmon revision is from jayofelony's maintained nexmon branch
  because it includes drivers for 6.12/6.18 absent in seemoo-lab master.
  This reuses only nexmon firmware/driver/tools, never a pwnagotchi image or
  supervisor. Original repositories retain their respective licenses;
  this repo does not vendor their source or rewrite their licensing.
- **Real target headers:** the image compiles a driver for every installed
  Pi 4 v7l kernel and fails if matching headers/driver source are absent.
  The build host's `uname -r` is never used to select target drivers. The
  firmware is placed under `/lib/firmware/updates/brcm/` with the Pi 4
  board alias; packaged stock firmware is preserved. Initramfs is refreshed
  after installation. Monitor/injection capability remains a hardware gate.
  A small build-time adapter checks the **actual** `set_monitor_channel`
  declaration in the target cfg80211 header. Newer 6.12 point releases add a
  `net_device` argument; the adapter accepts it while preserving nexmon's
  existing PHY-wide channel control. Unknown signatures fail the build.
  This local adjustment is tracked in redux source and recorded, with the
  installed driver's checksum, under `/usr/share/redux/nexmon-driver-*`.
  It also reuses the maintained nexmon 6.18 driver's compatibility alias
  when the target header renames `SDIO_DEVICE_ID_BROADCOM_CYPRESS_43752`;
  the device ID value always comes from the actual kernel header.
- **Kernel held:** image kernel packages are held to avoid an APT update
  replacing the kernel without rebuilding its nexmon module. Rebuild the
  image to upgrade the pair; do not assume firmware alone is sufficient.
- **Lean runtime:** temporary compiler tools, headers and nexmon build
  sources are removed. bettercap is installed from Pi OS/Debian APT, with
  no Kali repository or tool pack. redux is copied as a stdlib-only package
  to `/opt/redux`, so boot needs no pip, virtualenv or network download.
- **Auditability, not bit-for-bit reproduction:** sources are pinned but
  APT repositories are live. Actual package versions, target kernels,
  source revisions and firmware SHA256 are recorded in `/usr/share/redux/`.
  Build logs are the evidence of compilation; no hardware success is inferred.

Sources: [pi-gen Bookworm](https://github.com/RPi-Distro/pi-gen/tree/1c2abf50924d5bfee3527657af74ddfb1da52904),
[maintained nexmon source](https://github.com/jayofelony/nexmon/tree/1654e1857766df92086dbfbed5ffd288efc9bd8c),
[original nexmon project](https://github.com/seemoo-lab/nexmon).

## Verification

No-hardware gate:

```sh
pip install -e '.[dev]'
python -m compileall redux
pytest
for f in $(git ls-files '*.sh'); do bash -n "$f"; done
```

Tests execute the real preparation and stage scripts against local source
fixtures, check service/source placement and single-image export, ensure
existing build directories are refused, and check bootstrap SIGTERM shutdown.
They do **not** claim to compile the ARM image or exercise real firmware.

**Physical acceptance gate (not yet recorded):**

1. Run `sudo ./build.sh` to completion and save the deploy logs/checksums.
2. Flash that `.img.xz` using Raspberry Pi Imager onto a spare SD card.
3. Boot a Pi 4 with local console access. Provision an operator if needed.
4. Record the actual output of:

   ```sh
   uname -a
   systemctl is-enabled redux.service
   systemctl is-active redux.service
   journalctl -b -u redux.service --no-pager
   command -v bettercap nexutil
   modinfo -n brcmfmac
   cat /usr/share/redux/nexmon-kernels.txt
   sha256sum -c /usr/share/redux/nexmon-firmware.sha256
   sha256sum -c /usr/share/redux/nexmon-driver.sha256
   systemctl is-enabled bettercap.service   # expected: masked (exit nonzero)
   ```

5. Verify `brcmfmac` resolves to the installed kernel's `updates/brcmfmac.ko`.
   Record Pi model, image SHA256 and service output in the PR. `redux.service`
   must be enabled and active after a second boot. Task 1.1 stays claimed
   until a reviewer records that gate; CI green alone does not satisfy it.
