#!/usr/bin/env bash
# Temporary Pi 4 field correction, applied from the trusted laptop over SSH.
# This intentionally changes ONLY the live radio service's runtime environment
# plus the operator-selected wireless regulatory domain and soft rfkill state.
# /run systemd overrides do not persist; the permanent HOME fix is in the image.
set -euo pipefail

if [[ ${1:-} == --help || $# -ne 1 || ! ${1:-} =~ ^[A-Z]{2}$ ]]; then
  echo 'Usage: sudo bash -s -- <TWO-LETTER-PHYSICAL-COUNTRY>' >&2
  echo 'Example: US only when physically using this Pi in the United States.' >&2
  exit 2
fi
if [[ $EUID -ne 0 ]]; then
  echo 'Run remotely through sudo (see docs/FIELD_TEST.md).' >&2
  exit 2
fi
country=$1
if [[ ! -d /captures/live || ! -f /etc/redux/live.toml ]]; then
  echo 'Redux live state/config unavailable; leaving service unchanged.' >&2
  exit 3
fi
if ! grep -Eq '^[[:space:]]*state_dir[[:space:]]*=[[:space:]]*"/captures/live"' /etc/redux/live.toml; then
  echo 'Unexpected state_dir; refusing HOME override.' >&2
  exit 3
fi

echo 'Applying temporary HOME override to Redux live service...'
systemctl stop redux-live.service
install -d -m 0755 /run/systemd/system/redux-live.service.d
printf '[Service]\nEnvironment=HOME=/captures/live\n' \
  > /run/systemd/system/redux-live.service.d/field-home.conf
systemctl daemon-reload

echo "Applying operator-specified regulatory country $country and clearing Wi-Fi soft rfkill..."
if ! iw reg set "$country"; then
  echo 'Regulatory setting failed; leaving radio blocked, but applying HOME correction.' >&2
else
  rfkill unblock wifi || echo 'WARNING: rfkill unblock did not succeed' >&2
fi
systemctl restart redux-live.service
sleep 7

echo
echo '=== Current state (no packet data or credentials) ==='
redux-report --full
echo
echo 'HOME correction and regulatory setting are for THIS BOOT only.'
echo 'No existing PCAPs, the primary Pwnagotchi card or SSH configuration changed.'
