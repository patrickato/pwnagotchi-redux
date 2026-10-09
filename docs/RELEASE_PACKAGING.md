# Redux development image: build, package, and validate

This branch includes the complete pi-gen image source, the passive Redux live
supervisor, the native capture-processing units, and the standalone release
packager. **No file produced from the source is considered a tested Pi image
until it has booted and passed physical acceptance tests.**

## Build host

Follow the host prerequisite list in [IMAGE_BUILD.md](IMAGE_BUILD.md).
A Debian/Ubuntu Linux host must support privileged loop devices, partition
tools, `arch-test arm64`, `qemu-aarch64` binfmt, and sufficient free disk
space. This cannot be performed by hardware-free CI.

From a clean checkout of `human/redux-capture-pipeline-integration`:

```bash
git status --short
git rev-parse HEAD
sudo ./build.sh
```

The build writes an `.img.xz` under `build/image/pi-gen/deploy`, if the
ARM64 export succeeds. Do not relabel a `--prepare-only` run as an image build.

## Package that exact exported image

Use a fresh output directory, then:

```bash
python3 scripts/package_image.py \
  --deploy build/image/pi-gen/deploy \
  --out release/redux-pi-test \
  --revision "$(git rev-parse HEAD)"
cd release/redux-pi-test
sha256sum -c *.sha256
```

The output contains the original exported image (not transformed or repacked),
an SHA-256 sidecar, and a JSON manifest that names the full source revision,
artifact size and digest. If the deploy directory contains multiple images,
pass `--image FILENAME` explicitly. Existing release files are never
overwritten. The manifest deliberately labels `hardware_validated: false`.

## Flash the separate test card

Use Raspberry Pi Imager (Choose OS → Use custom) with the exported `.img.xz`,
or use the repo's existing [flash helper](../image/flash.sh) with its explicit
hash and confirmation steps. Never overwrite the fallback SD card.

The image itself starts `redux.service`, `redux-live.service` and the
passive ingestion timer. The live supervisor uses a single Bettercap child;
active capture files stay in `/captures/active` until a controlled
session rotation or shutdown atomically moves them into
`/captures/incoming`. The converter reads incoming only. The optional
Hashcat audit timer remains disabled.

## First-boot acceptance

Record hardware model, attached adapters, kernel, image SHA-256 and test date.
Inspect `/captures` mount, check both services are healthy, verify no
duplicate Bettercap process, then use:

```bash
sudo bash scripts/redux-live-diag.sh
redux live status
sudo journalctl -u redux-live.service -n 60 --no-pager
sudo journalctl -u redux-capture-ingest.service -n 60 --no-pager
```

Perform a passive test against your own access point, observe a real capture,
wait for file rotation, confirm conversion and status counters, then test
engine restart, radio hot-unplug, and reboot. Check storage exhaustion and
low-voltage conditions separately. No fake 22000 fixture can replace this.

The built-in TFT/touch acceptance and Pi-local Hashcat backend capability
are separate release gates. Only after **both** image boot and pipeline
functionality are proven should a hardware-tested release be labeled as such.
