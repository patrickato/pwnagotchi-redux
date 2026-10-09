#!/bin/bash
set -euo pipefail
# recurse=0 protects root while leaving the dedicated captures filesystem writable.
install -m 0644 files/overlayroot.conf "$ROOTFS_DIR/etc/overlayroot.conf"
install -d "$ROOTFS_DIR/captures" "$ROOTFS_DIR/etc/systemd/journald.conf.d"
install -m 0644 files/journal.conf "$ROOTFS_DIR/etc/systemd/journald.conf.d/redux.conf"
printf '\nLABEL=REDUXCAP /captures ext4 rw,noatime,nodev,nosuid,errors=remount-ro 0 2\n' >> "$ROOTFS_DIR/etc/fstab"
# Firmware is immutable at runtime. Maintenance requires an explicit remount.
sed -i '/[[:space:]]\/boot\/firmware[[:space:]]/s/defaults/ro,nodev,nosuid/; /[[:space:]]\/boot\/firmware[[:space:]]/s/0[[:space:]]*2$/0 0/' "$ROOTFS_DIR/etc/fstab"
sed -i '/^StateDirectory=/d; /\[Unit\]/a RequiresMountsFor=/captures' "$ROOTFS_DIR/etc/systemd/system/redux.service"
sed -i '/\[Service\]/a Environment=REDUX_STATE_DIR=/captures/boot\nReadWritePaths=/captures' "$ROOTFS_DIR/etc/systemd/system/redux.service"
on_chroot <<'EOF'
systemctl mask dphys-swapfile.service
EOF
