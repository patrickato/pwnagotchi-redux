#!/usr/bin/env bash
set -euo pipefail

REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
usage() {
    cat <<'EOF'
Usage: ./build.sh [--prepare-only]
Build an arm64 Raspberry Pi 4/5 image on a Debian/Ubuntu Linux host as root.
--prepare-only stages pinned sources and configuration without building an image.
Optional: REDUX_BUILD_DIR (default build/image), REDUX_IMAGE_CONFIG (local config).
See docs/IMAGE_BUILD.md for host dependencies and the physical boot gate.
EOF
}
PREPARE_ONLY=0
[[ $# -le 1 ]] || { usage >&2; exit 2; }
case ${1:-} in
    --help|-h) usage; exit 0 ;;
    --prepare-only) PREPARE_ONLY=1 ;;
    '') ;;
    *) usage >&2; exit 2 ;;
esac
[[ $(uname -s) == Linux ]] || { echo 'Build on Linux with an ext4 workspace; see docs/IMAGE_BUILD.md.' >&2; exit 1; }
if [[ $PREPARE_ONLY == 0 && $EUID != 0 ]]; then
    echo 'Image creation needs root for chroot and loop mounts. Run sudo ./build.sh.' >&2
    exit 1
fi
for tool in git realpath install tar python3; do
    command -v "$tool" >/dev/null || { echo "Missing build prerequisite: $tool" >&2; exit 1; }
done
if [[ $PREPARE_ONLY == 0 ]]; then
    command -v arch-test >/dev/null || { echo 'Missing arch-test; install the host prerequisites in docs/IMAGE_BUILD.md.' >&2; exit 1; }
    arch-test arm64 >/dev/null || { echo 'Host cannot execute arm64 binaries. Enable qemu-aarch64 binfmt_misc; see docs/IMAGE_BUILD.md.' >&2; exit 1; }
fi
BUILD_DIR=$(realpath -m -- "${REDUX_BUILD_DIR:-$REPO_DIR/build/image}")
[[ $BUILD_DIR != *' '* ]] || { echo 'pi-gen requires a build path without spaces.' >&2; exit 1; }
[[ ! -e $BUILD_DIR ]] || { echo "Build directory already exists: $BUILD_DIR. Choose a fresh REDUX_BUILD_DIR." >&2; exit 1; }
[[ $REPO_DIR != *' '* ]] || { echo 'Use a repository path without spaces for pi-gen.' >&2; exit 1; }
source "$REPO_DIR/image/sources.sh"
mkdir -p "$BUILD_DIR"

checkout_source() {
    local url=$1 rev=$2 dest=$3
    git init -q "$dest"
    git -C "$dest" remote add origin "$url"
    git -C "$dest" fetch --depth 1 origin "$rev"
    git -C "$dest" checkout -q --detach FETCH_HEAD
    [[ $(git -C "$dest" rev-parse HEAD) == "$rev" ]] || { echo "Source revision mismatch: $dest" >&2; exit 1; }
}
checkout_source "$PI_GEN_URL" "$PI_GEN_REV" "$BUILD_DIR/pi-gen"
# Fetch nexmon lazily: use the distro compiler, excluding bundled compilers.
NEXMON_DIR="$BUILD_DIR/nexmon"
git init -q "$NEXMON_DIR"
git -C "$NEXMON_DIR" remote add origin "$NEXMON_URL"
git -C "$NEXMON_DIR" fetch --depth 1 --filter=blob:none origin "$NEXMON_REV"
git -C "$NEXMON_DIR" sparse-checkout init --cone
git -C "$NEXMON_DIR" sparse-checkout set \
    buildtools/gcc-nexmon-plugin-arm buildtools/gcc-nexmon-plugin \
    buildtools/flash_patch_extractor buildtools/ucode_extractor buildtools/scripts \
    buildtools/b43 buildtools/b43-v2 buildtools/b43-v3 \
    firmwares/bcm43455c0/7_45_206 patches/bcm43455c0/7_45_206 \
    patches/common patches/include patches/driver \
    utilities/nexutil utilities/libnexio utilities/libargp
git -C "$NEXMON_DIR" checkout -q --detach FETCH_HEAD
[[ $(git -C "$NEXMON_DIR" rev-parse HEAD) == "$NEXMON_REV" ]]

PI_GEN_DIR="$BUILD_DIR/pi-gen"
cp -a "$REPO_DIR/image/stage-redux" "$PI_GEN_DIR/stage-redux"
mkdir -p "$PI_GEN_DIR/stage-redux/20-boot-budget/files"
cp -a "$REPO_DIR/boot" "$PI_GEN_DIR/stage-redux/20-boot-budget/files/boot"
mkdir -p "$PI_GEN_DIR/stage-redux/40-power/files"
cp -a "$REPO_DIR/boot" "$PI_GEN_DIR/stage-redux/40-power/files/boot"
mkdir -p "$PI_GEN_DIR/stage-redux/50-rauc/files"
cp -a "$REPO_DIR/boot" "$PI_GEN_DIR/stage-redux/50-rauc/files/boot"
mkdir -p "$PI_GEN_DIR/stage-redux/60-packs/files"
cp -a "$REPO_DIR/boot" "$PI_GEN_DIR/stage-redux/60-packs/files/boot"
if [[ -n ${REDUX_OTA_CERT:-} ]]; then
    command -v openssl >/dev/null || { echo 'OTA profile needs openssl for certificate validation.' >&2; exit 1; }
    ! grep -q 'PRIVATE KEY' "$REDUX_OTA_CERT" || { echo 'Only public update certificates may be copied into images.' >&2; exit 1; }
    openssl x509 -in "$REDUX_OTA_CERT" -noout
    install -m 0644 "$REDUX_OTA_CERT" "$PI_GEN_DIR/stage-redux/50-rauc/files/keyring.pem"
fi
if [[ -n ${REDUX_POWER_CONFIG:-} ]]; then
    python3 -m json.tool "$REDUX_POWER_CONFIG" >/dev/null
    install -m 0644 "$REDUX_POWER_CONFIG" "$PI_GEN_DIR/stage-redux/40-power/files/power.json"
fi
case ${REDUX_TFT_PROFILE:-none} in
    none) ;;
    mpi3501) touch "$PI_GEN_DIR/stage-redux/40-power/files/mpi3501" ;;
    *) echo 'Unsupported TFT profile; select none or mpi3501.' >&2; exit 1 ;;
esac
mkdir -p "$PI_GEN_DIR/stage-redux/00-redux/files/redux"
cp -a "$REPO_DIR/redux/." "$PI_GEN_DIR/stage-redux/00-redux/files/redux/"
# Stage the exact same capture configuration and unit templates as the source checkout.
install -d "$PI_GEN_DIR/stage-redux/00-redux/files/pipeline"
cp "$REPO_DIR/config/pipeline.example.toml" "$PI_GEN_DIR/stage-redux/00-redux/files/pipeline/pipeline.toml"
cp "$REPO_DIR/config/live.example.toml" "$PI_GEN_DIR/stage-redux/00-redux/files/pipeline/live.toml"
for unit in redux-capture-ingest redux-capture-audit; do
    cp "$REPO_DIR/systemd/$unit.service" "$PI_GEN_DIR/stage-redux/00-redux/files/pipeline/"
    cp "$REPO_DIR/systemd/$unit.timer" "$PI_GEN_DIR/stage-redux/00-redux/files/pipeline/"
done
# Do not ship development bytecode or require pip/network access at boot.
find "$PI_GEN_DIR/stage-redux/00-redux/files/redux" -name '__pycache__' -type d -exec rm -r -- {} +
mv "$NEXMON_DIR" "$PI_GEN_DIR/stage-redux/00-redux/files/nexmon"
cp "$REPO_DIR/image/sources.sh" "$PI_GEN_DIR/stage-redux/00-redux/files/sources.sh"
git -C "$REPO_DIR" rev-parse HEAD > "$PI_GEN_DIR/stage-redux/00-redux/files/redux-revision"
[[ -z $(git -C "$REPO_DIR" status --porcelain --untracked-files=normal) ]] || printf '%s\n' 'dirty working tree' >> "$PI_GEN_DIR/stage-redux/00-redux/files/redux-revision"
cp "$REPO_DIR/image/config" "$PI_GEN_DIR/config"
if [[ -n ${REDUX_IMAGE_CONFIG:-} ]]; then
    cat "$REDUX_IMAGE_CONFIG" >> "$PI_GEN_DIR/config"
fi
# These topology choices cannot be overridden by local login/locale settings.
cat >> "$PI_GEN_DIR/config" <<'EOF'
RELEASE=bookworm
STAGE_LIST="stage0 stage1 stage2 stage-redux"
EOF
touch "$PI_GEN_DIR/stage2/SKIP_IMAGES"
# Both supported boards use arm64, with their respective kernel packages.
cat > "$PI_GEN_DIR/stage0/02-firmware/01-packages" <<'EOF'
initramfs-tools
raspi-firmware
linux-image-rpi-v8
linux-image-rpi-2712
linux-headers-rpi-v8
linux-headers-rpi-2712
EOF
chmod +x "$PI_GEN_DIR/stage-redux/prerun.sh" "$PI_GEN_DIR/stage-redux/00-redux/00-run.sh"
python3 "$REPO_DIR/image/export_layout.py" "$PI_GEN_DIR/export-image/prerun.sh"
if [[ -n ${REDUX_OTA_CERT:-} ]]; then
    python3 "$REPO_DIR/image/prepare_ota.py" "$REPO_DIR/image" "$PI_GEN_DIR"
fi
chmod +x "$PI_GEN_DIR/stage-redux/10-overlay/00-run.sh"
chmod +x "$PI_GEN_DIR/stage-redux/20-boot-budget/00-run.sh"
chmod +x "$PI_GEN_DIR/stage-redux/30-watchdog/00-run.sh"
chmod +x "$PI_GEN_DIR/stage-redux/40-power/00-run.sh"
chmod +x "$PI_GEN_DIR/stage-redux/50-rauc/00-run.sh"
chmod +x "$PI_GEN_DIR/stage-redux/60-packs/00-run.sh"
echo "Prepared arm64 Pi 4/5 source tree: $PI_GEN_DIR"
echo 'Reason: Lite stages only; nexmon built for installed Pi kernel, never the host kernel.'
if [[ $PREPARE_ONLY == 1 ]]; then
    echo 'Preparation only: no image has been built.'
    exit 0
fi
cd "$PI_GEN_DIR"
./build.sh
echo "Image and build logs: $PI_GEN_DIR/deploy"
