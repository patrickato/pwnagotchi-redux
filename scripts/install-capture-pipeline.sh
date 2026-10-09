#!/usr/bin/env bash
# Install Redux passive capture processing on a standalone systemd image.
set -euo pipefail

case "${1:-}" in
  "") ENABLE=0 ;;
  --enable-ingest) ENABLE=1 ;;
  --help) echo "sudo bash scripts/install-capture-pipeline.sh [--enable-ingest]"; exit 0 ;;
  *) echo "Unsupported argument" >&2; exit 2 ;;
esac

if [[ "$EUID" -ne 0 ]]; then
  echo "Run with sudo" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="/opt/redux-pipeline/app"
CFG="/etc/redux/pipeline.toml"
PY="/usr/bin/python3"

if [[ ! -f "$ROOT/pyproject.toml" || ! -f "$ROOT/redux/crack/ingest.py" ]]; then
  echo "This must be run from a complete, integrated Redux source tree" >&2
  exit 1
fi
if [[ ! -x "$PY" ]] || ! "$PY" -c 'import sys; assert sys.version_info >= (3,11)'; then
  echo "Python 3.11+ required" >&2
  exit 1
fi
command -v systemctl >/dev/null || { echo "systemd required" >&2; exit 1; }

install -d -m 0755 "$APP" /etc/redux
install -d -m 0700 /var/lib/redux/captures
for name in incoming ready audits; do
  install -d -m 0700 "/var/lib/redux/captures/$name"
done
rm -rf "$APP/redux"
cp -a "$ROOT/redux" "$APP/redux"
chmod -R a+rX "$APP/redux"
if [[ ! -f "$CFG" ]]; then
  install -m 0600 "$ROOT/config/pipeline.example.toml" "$CFG"
fi
PYTHONPATH="$APP" "$PY" -c 'from redux.crack.ingest import CaptureIngestor; from redux.crack.audit import AuditWorker'

for name in redux-capture-ingest redux-capture-audit; do
  temporary="$(mktemp)"
  sed "/^\[Service\]$/a Environment=PYTHONPATH=$APP" "$ROOT/systemd/$name.service" > "$temporary"
  install -m 0644 "$temporary" "/etc/systemd/system/$name.service"
  rm -f "$temporary"
  install -m 0644 "$ROOT/systemd/$name.timer" "/etc/systemd/system/$name.timer"
done

systemctl daemon-reload
if [[ "$ENABLE" -eq 1 ]]; then
  systemctl enable --now redux-capture-ingest.timer
  echo "Passive ingest timer enabled; audit remains disabled"
else
  echo "Services installed but not enabled"
fi
if ! command -v hcxpcapngtool >/dev/null; then
  echo "NOTE: hcxtools is required to convert raw .pcap/.pcapng files"
fi
if ! command -v hashcat >/dev/null; then
  echo "NOTE: Hashcat not found; audit requires a compatible backend"
fi
echo "Configuration: $CFG"
echo "Try: sudo env PYTHONPATH=$APP $PY -m redux.crack.ingest --config $CFG --once"
