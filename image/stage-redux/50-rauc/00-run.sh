#!/bin/bash
set -euo pipefail
if [[ ! -f files/keyring.pem ]]; then
    echo 'RAUC disabled: no operator-provided public update trust certificate.'
    on_chroot <<'EOF'
systemctl mask rauc.service
EOF
    exit 0
fi
install -d "$ROOTFS_DIR/opt/redux/boot" "$ROOTFS_DIR/etc/rauc" "$ROOTFS_DIR/usr/local/libexec" "$ROOTFS_DIR/usr/local/bin" "$ROOTFS_DIR/etc/systemd/system/rauc.service.d" "$ROOTFS_DIR/usr/lib/systemd/system-generators" "$ROOTFS_DIR/boot/redux-control"
install -m 0644 files/boot/ota.py "$ROOTFS_DIR/opt/redux/boot/ota.py"
install -m 0644 files/keyring.pem "$ROOTFS_DIR/etc/rauc/keyring.pem"
ln -s /run/redux-ota/system.conf "$ROOTFS_DIR/etc/rauc/system.conf"
for mode in backend generator health preinstall deadline; do
    printf '#!/bin/sh\nexport PYTHONPATH=/opt/redux\nexec /usr/bin/python3 /opt/redux/boot/ota.py %s "$@"\n' "$mode" > "$ROOTFS_DIR/usr/local/libexec/redux-rauc-$mode"
    chmod 0755 "$ROOTFS_DIR/usr/local/libexec/redux-rauc-$mode"
done
ln -s /usr/local/libexec/redux-rauc-generator "$ROOTFS_DIR/usr/lib/systemd/system-generators/redux-ota"
printf '#!/bin/sh\nexport PYTHONPATH=/opt/redux\nexec /usr/bin/python3 /opt/redux/boot/ota.py "$@"\n' > "$ROOTFS_DIR/usr/local/bin/redux-ota"
chmod 0755 "$ROOTFS_DIR/usr/local/bin/redux-ota"
install -m 0644 files/redux-ota-*.service files/redux-ota-*.timer "$ROOTFS_DIR/etc/systemd/system/"
install -m 0644 files/rauc.conf "$ROOTFS_DIR/etc/systemd/system/rauc.service.d/redux.conf"
printf '\n[all]\nkernel_watchdog_timeout=60\n' >> "$ROOTFS_DIR/boot/firmware/config.txt"
on_chroot <<'EOF'
systemctl enable redux-ota-health.service
systemctl enable redux-ota-deadline.timer
systemctl unmask rauc.service
EOF
