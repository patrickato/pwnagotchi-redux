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
# Headless build: never let a package's debconf dialog stall the chroot. The
# toolset pulls wireshark-common and kismet, whose setuid questions otherwise
# pop an interactive blue screen that hangs a pi-gen build. Force the
# noninteractive frontend for every apt-get below and preseed the known setuid
# prompts to safe defaults (capture runs as root; no setuid-root helpers needed).
export DEBIAN_FRONTEND=noninteractive
echo 'wireshark-common wireshark-common/install-setuid boolean false' | debconf-set-selections 2>/dev/null || true
echo 'kismet-capture-common kismet-capture-common/install-setuid boolean false' | debconf-set-selections 2>/dev/null || true
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
# GPS: the standard Linux gpsd owns the USB/serial GPS; the runtime reads fixes
# over localhost:2947 (REDUX_GPSD=1 in redux-live.service). USBAUTO lets gpsd pick
# up a hotplugged or boot-present USB GPS on any tty path. Enabled by default and
# harmless with no GPS attached. gpsd is installed tolerantly here so this works
# regardless of whether the toolset stage (which also ships gpsd) ran first.
apt-get install -y --no-install-recommends gpsd gpsd-clients >/dev/null 2>&1 || true
printf '%s\n' 'START_DAEMON="true"' 'USBAUTO="true"' 'DEVICES=""' 'GPSD_OPTIONS="-n"' \
    > /etc/default/gpsd
systemctl enable gpsd.socket 2>/dev/null || true
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
# --- Comprehensive operator toolset -----------------------------------------
# Bake in the Wi-Fi / capture / recon / crack / web / Bluetooth / SDR / forensics
# / monitoring toolkit so a fresh flash is field-ready without hunting packages.
# Installed TOLERANTLY: the whole set is tried in one batch first, and on any
# unavailable package it falls back to per-package installs, so a single bad name
# can never fail the image build. (Indiscriminate emitters -- mdk*, beacon/BLE
# flooders, RF jammers -- are deliberately excluded: they cannot be aimed, hit
# bystanders, and are illegal to transmit.)
REDUX_TOOLSET="aircrack-ng hcxdumptool reaver pixiewps kismet wavemon horst \
wireless-tools tcpdump tshark ngrep nmap masscan arp-scan netdiscover nbtscan \
hping3 fping mtr-tiny bind9-dnsutils whois socat netcat-openbsd hashcat john \
hydra medusa ncrack crunch cewl hashid sqlmap nikto whatweb dirb wfuzz bluez \
bluez-tools rtl-sdr rtl-433 multimon-ng hackrf gpsd gpsd-clients binwalk foremost \
libimage-exiftool-perl steghide python3-scapy python3-impacket python3-pip htop \
btop iftop iotop nload tmux screen jq vim tree ncdu rsync usbutils pciutils \
i2c-tools net-tools"
if ! apt-get install -y --no-install-recommends $REDUX_TOOLSET; then
    echo "redux toolset: batch install hit an unavailable package; retrying per-package"
    for _pkg in $REDUX_TOOLSET; do
        apt-get install -y --no-install-recommends "$_pkg" \
            || echo "  redux toolset SKIP (unavailable on this suite): $_pkg"
    done
fi
# iw/ip/rfkill live in /usr/sbin; make sure the operator's login shell finds them
# (the redux CLI also handles this itself, but a plain `which iw` should work too).
printf '%s\n' 'case ":${PATH}:" in *:/usr/sbin:*) ;; *) PATH="${PATH}:/usr/sbin:/sbin"; export PATH ;; esac' \
    > /etc/profile.d/redux-path.sh
chmod 0644 /etc/profile.d/redux-path.sh
# AngryOxide (the preferred capture "scalpel"; Rust, no apt package). Best-effort
# bake of the upstream aarch64 release binary, resolved dynamically from the GitHub
# API so it tracks the latest version. Fully TOLERANT: missing curl / no network /
# unresolved asset all skip cleanly (redux/toolbox documents the manual install),
# and every network call is time-bounded so a blocked host can't stall the build.
if apt-get install -y --no-install-recommends curl >/dev/null 2>&1; then
    _aox_url="$(curl -fsSL --max-time 30 https://api.github.com/repos/Ragnt/AngryOxide/releases/latest 2>/dev/null \
        | grep -oE 'https://[^"]*aarch64[^"]*\.tar\.gz' | head -1)"
    if [ -n "$_aox_url" ] && curl -fsSL --max-time 120 "$_aox_url" -o /tmp/aox.tgz 2>/dev/null; then
        tar -xzf /tmp/aox.tgz -C /tmp 2>/dev/null \
            && install -m0755 "$(find /tmp -name angryoxide -type f 2>/dev/null | head -1)" /usr/bin/angryoxide 2>/dev/null \
            && echo "AngryOxide baked from $_aox_url" \
            || echo "AngryOxide extract/install skipped; use 'redux toolbox install angryoxide'"
        rm -f /tmp/aox.tgz
    else
        echo "AngryOxide release asset not resolved; use 'redux toolbox install angryoxide'"
    fi
fi
# Timers are shipped, but left disabled until output paths and writable
# mounts pass device acceptance checks.
systemctl disable redux-capture-audit.timer 2>/dev/null || true
# The engine ships as a binary only. No stock unit may start an unscoped session.
systemctl mask bettercap.service
# Keep the image lean; compile tools belong on the build host, not in the field.
apt-get purge -y build-essential gcc g++ cpp make gawk qpdf bison flex libfl-dev \
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
