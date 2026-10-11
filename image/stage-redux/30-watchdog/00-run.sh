#!/bin/bash
set -euo pipefail
if [[ ${REDUX_DEV_LOGIN:-0} == 1 ]]; then
    # Initial device qualification: keep the hardware reset timer disarmed.
    # The per-service systemd watchdog remains enabled for fault visibility.
    install -d "$ROOTFS_DIR/etc/systemd/system.conf.d"
    printf '[Manager]\nRuntimeWatchdogSec=0\nRebootWatchdogSec=0\n' > "$ROOTFS_DIR/etc/systemd/system.conf.d/redux-watchdog.conf"
else
    grep -qx bcm2835_wdt "$ROOTFS_DIR/etc/initramfs-tools/modules" || { echo 'Target kernel has no BCM watchdog support; refusing to claim hardware protection.' >&2; exit 1; }
    install -d "$ROOTFS_DIR/etc/systemd/system.conf.d" "$ROOTFS_DIR/etc/modules-load.d"
    install -m 0644 files/watchdog.conf "$ROOTFS_DIR/etc/systemd/system.conf.d/redux-watchdog.conf"
    printf 'bcm2835_wdt\n' > "$ROOTFS_DIR/etc/modules-load.d/redux-watchdog.conf"
    printf '\n[all]\ndtparam=watchdog=on\n' >> "$ROOTFS_DIR/boot/firmware/config.txt"
fi
sed -i 's/^Type=simple$/Type=notify/; /\[Service\]/a NotifyAccess=main\nWatchdogSec=15s\nTimeoutStartSec=30s\nTimeoutStopSec=15s' "$ROOTFS_DIR/etc/systemd/system/redux.service"
