#!/usr/bin/env bash
set -euo pipefail
install -d "$ROOTFS_DIR/opt/redux" "$ROOTFS_DIR/usr/local/src" "$ROOTFS_DIR/etc/systemd/system" "$ROOTFS_DIR/usr/share/redux"
cp -a files/redux "$ROOTFS_DIR/opt/redux/"
cp -a files/nexmon "$ROOTFS_DIR/usr/local/src/"
install -m 0644 files/sources.sh files/redux-revision "$ROOTFS_DIR/usr/share/redux/"
install -m 0644 files/redux.service "$ROOTFS_DIR/etc/systemd/system/redux.service"
install -m 0755 files/install-nexmon.sh "$ROOTFS_DIR/usr/local/src/install-nexmon.sh"
install -m 0644 files/target-kernel.sh "$ROOTFS_DIR/usr/local/src/target-kernel.sh"
install -m 0644 files/patch-nexmon-driver.py "$ROOTFS_DIR/usr/local/src/patch-nexmon-driver.py"
on_chroot <<'EOF'
/usr/local/src/install-nexmon.sh
useradd --system --home-dir /var/lib/redux --shell /usr/sbin/nologin redux
chown -R root:root /opt/redux
python3 -m compileall -q /opt/redux/redux
systemctl enable redux.service
# The engine ships as a binary only. No stock unit may start an unscoped session.
systemctl mask bettercap.service
# Keep the image lean; compile tools belong on the build host, not in the field.
apt-get purge -y build-essential gcc g++ cpp make git gawk qpdf bison flex libfl-dev \
    gcc-arm-none-eabi libnewlib-dev libgmp-dev libmpfr-dev libmpc-dev libnl-3-dev \
    libnl-genl-3-dev pkg-config linux-headers-rpi-v7l
apt-get autoremove -y
apt-get clean
rm -rf /usr/local/src/nexmon /usr/local/src/install-nexmon.sh /usr/local/src/target-kernel.sh /usr/local/src/patch-nexmon-driver.py
dpkg-query -W -f='${binary:Package}\t${Version}\n' > /usr/share/redux/packages.tsv
EOF
# Force the Pi 4 to use the v7l kernel we actually built the driver against.
cat >> "$ROOTFS_DIR/boot/firmware/config.txt" <<'EOF'

[pi4]
arm_64bit=0
[all]
EOF
