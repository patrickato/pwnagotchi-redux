# Image build — arm64 Pi 4 / Pi 5

`./build.sh` creates a new Pi OS Lite Bookworm arm64 image using pinned official
pi-gen stages 0–2 and `stage-redux`. It includes bettercap, patched BCM43455c0
nexmon firmware, nexutil, and `redux.service` (`python -m redux.core.boot`).
No pwnagotchi image or Python runtime is reused. The service currently logs its
bootstrap state and waits for shutdown; live integration belongs to the lead.
The stock bettercap unit is masked so installation cannot start an engine session.

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

The wrapper refuses existing build directories. Choose a fresh path for another
run with `sudo REDUX_BUILD_DIR="$PWD/build/image-next" ./build.sh`. Filesystem
mounts from a failed pi-gen run must be unmounted before removing any build tree.
`--prepare-only` stages sources without root or image creation; it is not a build.

Output: `build/image/pi-gen/deploy/image_*-pwnagotchi-redux-pi4-pi5-arm64.img.xz`.
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
writable. The export patch adds a third, 512 MiB ext4 partition labeled REDUXCAP
before loop attachment, formats it, and creates `/captures/redux` owned by the
service account with mode 0700. Unsupported upstream layout changes fail closed.
The root and captures sizes are separate; captures can be recovered independently.

Boot firmware mounts read-only. Swap is masked, the journal is volatile and capped
at 16 MiB, and redux requires the captures mount before starting. Service state
belongs in `/captures/redux`, never the ephemeral root. Captures uses ext4's journal;
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
