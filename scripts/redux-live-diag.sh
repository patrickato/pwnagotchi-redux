#!/usr/bin/env bash
# Read-only diagnostic summary for Redux live development-image testing.
set -uo pipefail

heading() { printf '\n== %s ==\n' "$1"; }
heading "System and mounts"
uname -a
mount | grep ' /captures ' || echo "REDUXCAP not mounted"
heading "Radio inventory (no packet collection)"
if command -v iw >/dev/null 2>&1; then iw dev; else echo "iw missing"; fi
heading "Service status"
systemctl --no-pager --full status redux.service redux-live.service redux-capture-ingest.timer 2>&1 | tail -n 65 || true
heading "Engine status (sanitized; does NOT print caplet credentials)"
if [[ -f /captures/redux/live.json ]]; then
  python3 -m json.tool /captures/redux/live.json || echo "status JSON unreadable"
else
  echo "No live status file"
fi
heading "Capture file counts and sizes (filenames only)"
if [[ -d /captures/incoming ]]; then
  find /captures/incoming -maxdepth 1 -type f \( -name '*.pcap' -o -name '*.pcapng' -o -name '*.hc22000' \) -printf '%f %s bytes\n' | head -n 40
else
  echo "Capture directory missing"
fi
heading "Binary presence"
for tool in bettercap hcxpcapngtool hashcat python3; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "$tool: present"
  else
    echo "$tool: not installed"
  fi
done
heading "Recent live service log"
journalctl -u redux-live.service -n 40 --no-pager 2>&1 || true
echo "Done. Never share /captures/redux/bettercap-live.cap, hash files, or recovered credentials."
