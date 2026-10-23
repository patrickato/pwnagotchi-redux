#!/bin/bash
set -euo pipefail
# pi-gen Bookworm stage2 still arms firstboot root resizing and resize2fs_once.
# Both are incompatible with REDUXCAP located AFTER the root partition.
# Remove the known firstboot token, disable the legacy service before deletion,
# and leave a persistent systemd mask so first boot cannot expand into captures.
python3 files/disable-resize.py "$ROOTFS_DIR"
on_chroot <<'EOF'
systemctl disable resize2fs_once.service 2>/dev/null || true
EOF
rm -f "$ROOTFS_DIR/etc/init.d/resize2fs_once"
ln -s /dev/null "$ROOTFS_DIR/etc/systemd/system/resize2fs_once.service"
# recurse=0 protects root while leaving the dedicated captures filesystem writable.
install -m 0644 files/overlayroot.conf "$ROOTFS_DIR/etc/overlayroot.conf"
install -d "$ROOTFS_DIR/captures" "$ROOTFS_DIR/etc/systemd/journald.conf.d"
install -m 0644 files/journal.conf "$ROOTFS_DIR/etc/systemd/journald.conf.d/redux.conf"
# nofail + a bounded device timeout so a missing/unmountable REDUXCAP never
# drops a headless field unit into emergency mode or a 90s boot stall: the box
# still boots, and the read-only diagnostics/Doctor reports the storage outage.
# fsck pass 0 (not 2): a non-blocking optional mount must not gate boot on fsck.
printf '\nLABEL=REDUXCAP /captures ext4 rw,noatime,nodev,nosuid,errors=remount-ro,nofail,x-systemd.device-timeout=30s 0 0\n' >> "$ROOTFS_DIR/etc/fstab"
# Firmware is immutable at runtime. Maintenance requires an explicit remount.
sed -i '/[[:space:]]\/boot\/firmware[[:space:]]/s/defaults/ro,nodev,nosuid/; /[[:space:]]\/boot\/firmware[[:space:]]/s/0[[:space:]]*2$/0 0/' "$ROOTFS_DIR/etc/fstab"
sed -i '/^StateDirectory=/d; /\[Unit\]/a RequiresMountsFor=/captures' "$ROOTFS_DIR/etc/systemd/system/redux.service"
sed -i '/\[Service\]/a Environment=REDUX_STATE_DIR=/captures/boot\nReadWritePaths=/captures' "$ROOTFS_DIR/etc/systemd/system/redux.service"
on_chroot <<'EOF'
systemctl mask dphys-swapfile.service
EOF
