#!/usr/bin/env bash
set -euo pipefail
install -d "$ROOTFS_DIR/opt/redux" "$ROOTFS_DIR/usr/local/src" "$ROOTFS_DIR/etc/systemd/system" "$ROOTFS_DIR/usr/share/redux"
cp -a files/redux "$ROOTFS_DIR/opt/redux/"
cp -a files/nexmon "$ROOTFS_DIR/usr/local/src/"
install -m 0644 files/sources.sh files/redux-revision "$ROOTFS_DIR/usr/share/redux/"
install -m 0644 files/redux.service "$ROOTFS_DIR/etc/systemd/system/redux.service"
install -m 0644 files/redux-bt-up.service "$ROOTFS_DIR/etc/systemd/system/redux-bt-up.service"
# The image copies Python sources directly; expose the same Redux CLI without
# relying on pip or a writable root filesystem after boot.
install -d "$ROOTFS_DIR/usr/local/bin"
cat > "$ROOTFS_DIR/usr/local/bin/redux" <<'REDUX_LAUNCHER'
#!/bin/sh
PYTHONPATH=/opt/redux exec /usr/bin/python3 -m redux.cli "$@"
REDUX_LAUNCHER
chmod 0755 "$ROOTFS_DIR/usr/local/bin/redux"
cat > "$ROOTFS_DIR/usr/local/bin/redux-kiosk" <<'REDUX_KIOSK_LAUNCHER'
#!/bin/sh
PYTHONPATH=/opt/redux exec /usr/bin/python3 -m redux.display.kiosk "$@"
REDUX_KIOSK_LAUNCHER
chmod 0755 "$ROOTFS_DIR/usr/local/bin/redux-kiosk"
cat > "$ROOTFS_DIR/usr/local/bin/redux-fbui" <<'REDUX_FBUI_LAUNCHER'
#!/bin/sh
PYTHONPATH=/opt/redux exec /usr/bin/python3 -m redux.display.fbui "$@"
REDUX_FBUI_LAUNCHER
chmod 0755 "$ROOTFS_DIR/usr/local/bin/redux-fbui"
# The lightweight 16-bit framebuffer renderer is packaged without X11 and
# never enabled automatically. Operators opt in after TFT pixel/touch checks.
install -m 0644 files/redux-fbui.service \
    "$ROOTFS_DIR/etc/systemd/system/redux-fbui.service"
install -m 0644 files/fbui.example.env \
    "$ROOTFS_DIR/usr/share/redux/fbui.example.env"
cat > "$ROOTFS_DIR/usr/local/bin/redux-display-diag" <<'REDUX_DISPLAY_DIAG_LAUNCHER'
#!/bin/sh
PYTHONPATH=/opt/redux exec /usr/bin/python3 -m redux.display.acceptance "$@"
REDUX_DISPLAY_DIAG_LAUNCHER
chmod 0755 "$ROOTFS_DIR/usr/local/bin/redux-display-diag"
# Headless first-boot mount, service, and image evidence: operator invoked only.
cat > "$ROOTFS_DIR/usr/local/bin/redux-boot-check" <<'REDUX_BOOT_CHECK_LAUNCHER'
#!/bin/sh
PYTHONPATH=/opt/redux exec /usr/bin/python3 -m redux.boot_readiness "$@"
REDUX_BOOT_CHECK_LAUNCHER
chmod 0755 "$ROOTFS_DIR/usr/local/bin/redux-boot-check"
# Record the browser profile only if explicitly requested by the build host.
# Never enable a desktop autostart unit or claim the TFT has passed acceptance.
if [[ -f files/kiosk-profile ]]; then
    install -m 0644 files/kiosk-profile "$ROOTFS_DIR/usr/share/redux/kiosk-profile"
    # Inactive user-scoped unit only; never start root Chromium or guess a TFT.
    install -d "$ROOTFS_DIR/usr/lib/systemd/user"
    install -m 0644 files/redux-kiosk.service \
        "$ROOTFS_DIR/usr/lib/systemd/user/redux-kiosk.service"
    install -m 0644 files/kiosk.example.env \
        "$ROOTFS_DIR/usr/share/redux/kiosk.example.env"
fi
# Preinstall processing workers into the standalone image. The read-only root
# stores code/config; jobs/results live on the dedicated writable /captures.
install -d "$ROOTFS_DIR/etc/redux" "$ROOTFS_DIR/captures"
install -m 0600 files/pipeline/pipeline.toml "$ROOTFS_DIR/etc/redux/pipeline.toml"
install -m 0600 files/pipeline/live.toml "$ROOTFS_DIR/etc/redux/live.toml"
# Country is opt-in at image build time. Without it, runtime remains fail-closed.
if [[ -f files/regulatory-country ]]; then
    install -m 0644 files/regulatory-country "$ROOTFS_DIR/etc/redux/regulatory-country"
fi
install -m 0644 files/redux-regdom.service "$ROOTFS_DIR/etc/systemd/system/redux-regdom.service"
install -m 0755 files/pipeline/redux-live-diag.sh "$ROOTFS_DIR/usr/local/bin/redux-live-diag"
install -d "$ROOTFS_DIR/usr/local/lib/redux"
install -m 0644 files/pipeline/redux-field-report.py "$ROOTFS_DIR/usr/local/lib/redux/report.py"
cat > "$ROOTFS_DIR/usr/local/bin/redux-report" <<'REDUX_REPORT_LAUNCHER'
#!/bin/sh
PYTHONPATH=/opt/redux exec /usr/bin/python3 /usr/local/lib/redux/report.py "$@"
REDUX_REPORT_LAUNCHER
chmod 0755 "$ROOTFS_DIR/usr/local/bin/redux-report"
ln -s redux-report "$ROOTFS_DIR/usr/local/bin/r"
install -m 0644 files/redux-field-report.service "$ROOTFS_DIR/etc/systemd/system/redux-field-report.service"
install -m 0644 files/redux-field-report.timer "$ROOTFS_DIR/etc/systemd/system/redux-field-report.timer"
if [[ -f files/field-ssh-profile ]]; then
    install -m 0644 files/field-ssh-profile "$ROOTFS_DIR/usr/share/redux/field-ssh-profile"
fi
install -m 0644 files/redux-live.service "$ROOTFS_DIR/etc/systemd/system/redux-live.service"
for unit in redux-capture-ingest redux-capture-audit; do
    sed -e '/^\[Unit\]$/a RequiresMountsFor=/captures' \
        -e '/^\[Service\]$/a Environment=PYTHONPATH=/opt/redux' \
        "files/pipeline/$unit.service" > "$ROOTFS_DIR/etc/systemd/system/$unit.service"
    install -m 0644 "files/pipeline/$unit.timer" "$ROOTFS_DIR/etc/systemd/system/$unit.timer"
done
install -m 0755 files/install-nexmon.sh "$ROOTFS_DIR/usr/local/src/install-nexmon.sh"
install -m 0644 files/target-kernel.sh "$ROOTFS_DIR/usr/local/src/target-kernel.sh"
install -m 0644 files/patch-nexmon-driver.py "$ROOTFS_DIR/usr/local/src/patch-nexmon-driver.py"
on_chroot <<'EOF'
/usr/local/src/install-nexmon.sh
useradd --system --home-dir /var/lib/redux --shell /usr/sbin/nologin redux
chown -R root:root /opt/redux
python3 -m compileall -q /opt/redux/redux
systemctl enable redux.service
# Radio owner starts independently; no process is launched if hardware is unavailable.
systemctl enable redux-regdom.service
systemctl enable redux-live.service
systemctl enable redux-field-report.timer
if [[ -f /usr/share/redux/field-ssh-profile ]]; then
    systemctl enable ssh.service avahi-daemon.service
fi
# Capture processing operates on the writable REDUXCAP partition, independently.
systemctl enable redux-capture-ingest.timer
# Bluetooth: onboard hci0 serves the phone-PAN link; a USB BT dongle (hci1) is the
# dedicated BLE radio. AutoEnable powers adapters at boot, and redux-bt-up clears
# any rfkill soft-block + powers a late-enumerating dongle. Both honoured only
# when BlueZ is present; harmless otherwise.
install -d /etc/bluetooth/main.conf.d
printf '%s\n' '[Policy]' 'AutoEnable=true' > /etc/bluetooth/main.conf.d/redux-autoenable.conf
systemctl enable bluetooth.service 2>/dev/null || true
systemctl enable redux-bt-up.service 2>/dev/null || true
# hcx conversion is bundled; Hashcat compute backend remains hardware-gated.
apt-get install -y --no-install-recommends hcxtools
# Timers are shipped, but left disabled until output paths and writable
# mounts pass device acceptance checks.
systemctl disable redux-capture-audit.timer 2>/dev/null || true
# The engine ships as a binary only. No stock unit may start an unscoped session.
systemctl mask bettercap.service
# Keep the image lean; compile tools belong on the build host, not in the field.
apt-get purge -y build-essential gcc g++ cpp make git gawk qpdf bison flex libfl-dev \
    gcc-arm-none-eabi libnewlib-dev libgmp-dev libmpfr-dev libmpc-dev libnl-3-dev \
    libnl-genl-3-dev pkg-config linux-headers-rpi-v8 linux-headers-rpi-2712
apt-get autoremove -y
apt-get clean
rm -rf /usr/local/src/nexmon /usr/local/src/install-nexmon.sh /usr/local/src/target-kernel.sh /usr/local/src/patch-nexmon-driver.py
EOF
# Both boards use their distro-selected arm64 kernel, built above.
cat >> "$ROOTFS_DIR/boot/firmware/config.txt" <<'EOF'

[all]
arm_64bit=1
EOF
