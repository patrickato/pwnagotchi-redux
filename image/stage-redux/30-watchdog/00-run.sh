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
# Make the watchdog directives idempotent. The shipped redux.service already
# carries NotifyAccess=/WatchdogSec= (it is a fully-formed unit, not a bare
# template), so a blind append would duplicate them -- and the first-boot unit
# readiness gate rejects any duplicate directive. Strip the four directives this
# stage owns before appending the canonical set, exactly as 10-overlay strips
# StateDirectory= before adding its own. 30-watchdog is then the single authority
# for the per-service watchdog settings regardless of what the source ships.
sed -i '/^NotifyAccess=/d; /^WatchdogSec=/d; /^TimeoutStartSec=/d; /^TimeoutStopSec=/d; s/^Type=simple$/Type=notify/; /\[Service\]/a NotifyAccess=main\nWatchdogSec=15s\nTimeoutStartSec=30s\nTimeoutStopSec=15s' "$ROOTFS_DIR/etc/systemd/system/redux.service"
