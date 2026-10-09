#!/usr/bin/env bash
set -euo pipefail
install -d "$ROOTFS_DIR/opt/redux" "$ROOTFS_DIR/usr/local/src" "$ROOTFS_DIR/etc/systemd/system" "$ROOTFS_DIR/usr/share/redux"
cp -a files/redux "$ROOTFS_DIR/opt/redux/"
cp -a files/nexmon "$ROOTFS_DIR/usr/local/src/"
install -m 0644 files/sources.sh files/redux-revision "$ROOTFS_DIR/usr/share/redux/"
install -m 0644 files/redux.service "$ROOTFS_DIR/etc/systemd/system/redux.service"
# The image copies Python sources directly; expose the same Redux CLI without
# relying on pip or a writable root filesystem after boot.
install -d "$ROOTFS_DIR/usr/local/bin"
cat > "$ROOTFS_DIR/usr/local/bin/redux" <<'REDUX_LAUNCHER'
#!/bin/sh
PYTHONPATH=/opt/redux exec /usr/bin/python3 -m redux.cli "$@"
REDUX_LAUNCHER
chmod 0755 "$ROOTFS_DIR/usr/local/bin/redux"
# Preinstall processing workers into the standalone image. The read-only root
# stores code/config; jobs/results live on the dedicated writable /captures.
install -d "$ROOTFS_DIR/etc/redux" "$ROOTFS_DIR/captures"
install -m 0600 files/pipeline/pipeline.toml "$ROOTFS_DIR/etc/redux/pipeline.toml"
install -m 0600 files/pipeline/live.toml "$ROOTFS_DIR/etc/redux/live.toml"
install -m 0755 files/pipeline/redux-live-diag.sh "$ROOTFS_DIR/usr/local/bin/redux-live-diag"
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
systemctl enable redux-live.service
# Capture processing operates on the writable REDUXCAP partition, independently.
systemctl enable redux-capture-ingest.timer
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
dpkg-query -W -f='${binary:Package}\t${Version}\n' > /usr/share/redux/packages.tsv
EOF
# Both boards use their distro-selected arm64 kernel, built above.
cat >> "$ROOTFS_DIR/boot/firmware/config.txt" <<'EOF'

[all]
arm_64bit=1
EOF
