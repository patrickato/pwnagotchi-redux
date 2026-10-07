#!/usr/bin/env bash
set -eo pipefail
# setup_env.sh is upstream shell code that does not support nounset.
cd /usr/local/src/nexmon
source setup_env.sh
source /usr/local/src/target-kernel.sh
[[ $PLATFORMUNAME == aarch64 ]] || {
    echo "Expected arm64 userspace under native execution or qemu-aarch64, got $PLATFORMUNAME" >&2
    exit 1
}
# The pinned maintained nexmon source uses distro arm-none-eabi GCC.
make -C buildtools
make -C firmwares/bcm43455c0/7_45_206
# Never use upstream's default 'all': it selects the build HOST's uname -r.
make -C patches/bcm43455c0/7_45_206/nexmon brcmfmac43455-sdio.bin
make -C utilities/nexutil USE_VENDOR_CMD=1
install -m 0755 utilities/nexutil/nexutil /usr/local/bin/nexutil
install -D -m 0644 LICENSE.txt /usr/share/doc/nexmon/LICENSE.txt
install -d /lib/firmware/updates/brcm
install -m 0644 patches/bcm43455c0/7_45_206/nexmon/brcmfmac43455-sdio.bin \
    /lib/firmware/updates/brcm/brcmfmac43455-sdio.bin
ln -sfn brcmfmac43455-sdio.bin '/lib/firmware/updates/brcm/brcmfmac43455-sdio.raspberrypi,4-model-b.bin'
ln -sfn brcmfmac43455-sdio.bin '/lib/firmware/updates/brcm/brcmfmac43455-sdio.raspberrypi,5-model-b.bin'
# Compile against installed target headers. The target kernel, not the host,
# determines which version of the maintained nexmon driver is compatible.
count=0
: > /usr/share/redux/nexmon-kernels.txt
: > /usr/share/redux/nexmon-driver-compat.txt
: > /usr/share/redux/nexmon-driver.sha256
for module_dir in /lib/modules/*; do
    [[ -d $module_dir ]] || continue
    kernel=${module_dir##*/}
    driver=$(nexmon_driver_for "$kernel" "$NEXMON_ROOT" "$module_dir")
    header="$module_dir/build/include/net/cfg80211.h"
    if [[ ! -f $header ]]; then
        headers=(/usr/src/linux-headers-"${kernel%%+*}"*common*/include/net/cfg80211.h)
        [[ ${#headers[@]} == 1 && -f ${headers[0]} ]] || { echo "Cannot identify cfg80211 header for target $kernel" >&2; exit 1; }
        header=${headers[0]}
    fi
    printf '%s: ' "$kernel" >> /usr/share/redux/nexmon-driver-compat.txt
    python3 /usr/local/src/patch-nexmon-driver.py "$header" "$driver/cfg80211.c" \
        --sdio-header "${header%/net/cfg80211.h}/linux/mmc/sdio_ids.h" \
        >> /usr/share/redux/nexmon-driver-compat.txt
    nexmon_build_module "$kernel" "$module_dir" "$driver"
    install -d "$module_dir/updates"
    install -m 0644 "$driver/brcmfmac.ko" "$module_dir/updates/brcmfmac.ko"
    sha256sum "$module_dir/updates/brcmfmac.ko" >> /usr/share/redux/nexmon-driver.sha256
    depmod -a "$kernel"
    # pi-gen disables initramfs updates during stages; its export finaliser
    # creates the boot initramfs after our updates module has been installed.
    printf '%s\n' "$kernel" >> /usr/share/redux/nexmon-kernels.txt
    count=$((count + 1))
done
[[ $count -gt 0 ]] || { echo 'No target Pi kernel found; refusing to export a partial nexmon image.' >&2; exit 1; }
sha256sum /lib/firmware/updates/brcm/brcmfmac43455-sdio.bin > /usr/share/redux/nexmon-firmware.sha256
apt-mark manual bettercap python3 iw rfkill linux-image-rpi-v8 linux-image-rpi-2712
# A kernel update could silently drop monitor support. Upgrade kernel + driver
# together by rebuilding the image rather than silently loading stock brcmfmac.
mapfile -t kernel_packages < <(dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\n' 'linux-image-*' | nexmon_installed_kernel_packages)
[[ ${#kernel_packages[@]} -gt 0 ]] || { echo 'No installed kernel packages to hold; refusing an unprotected kernel/driver pair.' >&2; exit 1; }
apt-mark hold "${kernel_packages[@]}"
