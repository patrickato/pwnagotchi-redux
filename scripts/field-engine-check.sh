#!/usr/bin/env bash
# Read-only, two-sample inspection for Redux engine startup on the disposable
# Pi field image. Does not log in to Bettercap, expose caplet/credentials,
# configure radios, or restart any services.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo 'Run via sudo -n over the existing SSH connection.' >&2
  exit 2
fi
if [[ ! -d /captures/live || ! -f /etc/redux/live.toml ]]; then
  echo 'Expected Redux field card was not detected.' >&2
  exit 3
fi

snapshot() {
  echo
  echo "===== $(date -u +%FT%TZ) ====="
  python3 - <<'PY'
import json
from pathlib import Path
p = Path("/captures/live/live.json")
try:
    d = json.loads(p.read_text())
except (OSError, ValueError) as exc:
    print("live_state_file:", type(exc).__name__)
else:
    for k in ("state", "iface", "last_error", "dashboard_active", "created",
              "handshakes", "last_handoff_error", "last_probe"):
        value = d.get(k)
        if value is not None:
            print(f"{k}: {str(value)[:180]}")
PY
  echo '== Bettercap process =='
  ps -C bettercap -o pid,etime,stat,comm --no-headers 2>/dev/null || true
  echo '== Engine REST listener (127.0.0.1:8081) =='
  python3 - <<'PY'
import socket
try:
    with socket.create_connection(("127.0.0.1", 8081), timeout=1.0):
        print("tcp_8081: accepting connections")
except OSError as exc:
    print(f"tcp_8081: unavailable ({type(exc).__name__})")
PY
}
snapshot
echo
echo 'Waiting 22 seconds to see whether the startup phase clears or retries...'
sleep 22
snapshot
echo
echo '== Redux live journal (recent, sanitized) =='
journalctl -u redux-live.service --no-pager -o cat -n 55 |
  python3 -c '
import re, sys
for line in sys.stdin:
    line = re.sub(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", "[MAC]", line)
    line = re.sub(r"(?i)(password\s*[=: ]\s*)\S+", r"\1[REDACTED]", line)
    line = re.sub(r"(?i)(authorization\s*:\s*basic\s+)\S+", r"\1[REDACTED]", line)
    print(line.strip()[:220])
' || true
echo
echo '== Bettercap log tail (sanitized) =='
python3 - <<'PY'
from pathlib import Path
import re
p = Path("/captures/live/bettercap.log")
try:
    with p.open("rb") as f:
        f.seek(max(0, p.stat().st_size - 8192))
        lines = f.read(8192).decode("utf-8", "replace").splitlines()[-26:]
    for line in lines:
        line = re.sub(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", "[MAC]", line)
        line = re.sub(r"(?i)(password\s*[=: ]\s*)\S+", r"\1[REDACTED]", line)
        print(line[:220])
except OSError as exc:
    print("bettercap.log:", type(exc).__name__)
PY
echo '== End: read-only diagnostics; no configuration changed =='
