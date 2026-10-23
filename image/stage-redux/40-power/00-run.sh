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
if [[ -f files/tft35a ]]; then
    # Proven goodtft MPI3501 path (ILI9486 framebuffer + XPT2046 touch). The
    # overlay drives the panel upright at rotate=270 and registers the touch
    # controller, so a /dev/input/eventN appears for the fbui. Unlike the generic
    # fbtft overlay, this does NOT shear the panel.
    [[ -f files/tft35a.dtbo ]] || { echo 'tft35a profile selected but files/tft35a.dtbo was not staged.' >&2; exit 1; }
    install -d "$ROOTFS_DIR/boot/firmware/overlays"
    install -m 0644 files/tft35a.dtbo "$ROOTFS_DIR/boot/firmware/overlays/tft35a.dtbo"
    # The SPI panel must be a real framebuffer, not a KMS plane: comment out any
    # vc4-kms-v3d/vc4-fkms-v3d the base image shipped so the panel is /dev/fb1.
    if [[ -f $ROOTFS_DIR/boot/firmware/config.txt ]]; then
        sed -i -E 's/^[[:space:]]*(dtoverlay=vc4-(f)?kms-v3d.*)$/#\1/' \
            "$ROOTFS_DIR/boot/firmware/config.txt"
    fi
    printf '\n[all]\ndtparam=spi=on\ndtoverlay=tft35a:rotate=270\n' >> "$ROOTFS_DIR/boot/firmware/config.txt"
    # Seed assets: the validated fbui env and the first-boot seeder that copies it
    # onto the persistent /captures partition (where redux-fbui expects it).
    install -d "$ROOTFS_DIR/usr/share/redux"
    install -m 0644 files/fbui.seed.env "$ROOTFS_DIR/usr/share/redux/fbui.seed.env"
    install -m 0644 files/redux-fbui-seed.service "$ROOTFS_DIR/etc/systemd/system/redux-fbui-seed.service"
elif [[ -f files/mpi3501 ]]; then
    # Legacy generic fbtft overlay. Known to shear this panel and ships no touch
    # node; retained only for continuity. Prefer REDUX_TFT_PROFILE=tft35a.
    [[ -f $ROOTFS_DIR/boot/firmware/overlays/fbtft.dtbo ]] || { echo 'Target firmware lacks fbtft overlay.' >&2; exit 1; }
    printf '\n[all]\ndtparam=spi=on\ndtoverlay=fbtft,spi0-0,ili9486,reset_pin=25,dc_pin=24,rotate=90,speed=16000000\n' >> "$ROOTFS_DIR/boot/firmware/config.txt"
fi
on_chroot <<'EOF'
systemctl enable redux-power.service
EOF
# Only a validated-overlay (tft35a) image boots straight to a working screen, so
# only that profile enables the display services. A no-TFT or legacy image stays
# headless (redux-fbui remains installed-but-disabled from the 00-redux stage).
if [[ -f files/tft35a ]]; then
    on_chroot <<'EOF'
systemctl enable redux-fbui-seed.service
systemctl enable redux-fbui.service
EOF
fi
