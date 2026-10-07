#!/bin/bash
set -euo pipefail
install -d "$ROOTFS_DIR/opt/redux/boot" "$ROOTFS_DIR/etc/redux" "$ROOTFS_DIR/etc/systemd/system"
install -m 0644 files/boot/power.py files/boot/battery_tft.py "$ROOTFS_DIR/opt/redux/boot/"
install -m 0644 files/power.json "$ROOTFS_DIR/etc/redux/power.json"
install -m 0644 files/redux-power.service "$ROOTFS_DIR/etc/systemd/system/redux-power.service"
if python3 -c 'import json,sys; c=json.load(open("files/power.json")); sys.exit(not (c.get("enabled") is True and c.get("backend") == "geekworm"))'; then
    printf '\n[all]\ndtparam=i2c_arm=on\n' >> "$ROOTFS_DIR/boot/firmware/config.txt"
    printf 'i2c_dev\n' > "$ROOTFS_DIR/etc/modules-load.d/redux-ups.conf"
fi
if [[ -f files/mpi3501 ]]; then
    [[ -f $ROOTFS_DIR/boot/firmware/overlays/fbtft.dtbo ]] || { echo 'Target firmware lacks fbtft overlay.' >&2; exit 1; }
    printf '\n[all]\ndtparam=spi=on\ndtoverlay=fbtft,spi0-0,ili9486,reset_pin=25,dc_pin=24,rotate=90,speed=16000000\n' >> "$ROOTFS_DIR/boot/firmware/config.txt"
fi
on_chroot <<'EOF'
systemctl enable redux-power.service
EOF
