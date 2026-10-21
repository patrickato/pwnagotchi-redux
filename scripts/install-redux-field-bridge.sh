#!/usr/bin/env bash
# Separate opt-in user timer for bounded, read-only Pi observations.
# It never changes the Pi, Codex source timer, host SSH settings or firmware.
set -euo pipefail
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
UNIT_DIR="${HOME}/.config/systemd/user"
NAME="redux-field-bridge"

usage() {
  printf 'Usage: bash scripts/install-redux-field-bridge.sh --plan|--install|--status|--disable\n'
}

case "${1:---plan}" in
  --plan)
    printf 'Source-only plan. No local installation or Pi SSH attempted.\n'
    python3 "${BASE}/scripts/redux-field-bridge.py" plan
    printf 'Requires time-limited read-only enrollment before --install.\n'
    ;;
  --install)
    [[ "${BASE}" == "${HOME}/redux-workbench-src" ]] || {
      printf 'Refusing unexpected checkout location\n' >&2; exit 2;
    }
    [[ "$(git -C "${BASE}" branch --show-current)" == "human/redux-workbench" ]] || {
      printf 'Refusing incorrect branch\n' >&2; exit 2;
    }
    [[ -z "$(git -C "${BASE}" status --porcelain)" ]] || {
      printf 'Refusing dirty checkout\n' >&2; exit 2;
    }
    systemctl --user show-environment >/dev/null 2>&1 || {
      printf 'WSL systemd user manager unavailable\n' >&2; exit 2;
    }
    python3 -m pytest -q "${BASE}/tests/test_redux_field_bridge.py" || {
      printf 'Guardrail tests not passing; no timer installed\n' >&2; exit 2;
    }
    python3 -B - "${BASE}/scripts/redux-field-bridge.py" <<'PY'
from pathlib import Path
import importlib.util, sys, time
spec=importlib.util.spec_from_file_location("redux_field_bridge", sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    policy=module.load_policy()
    if not policy["enabled"] or policy["expires"] <= time.time() or policy["used"] >= policy["max_sessions"]:
        raise ValueError("approval inactive")
except (OSError, ValueError):
    raise SystemExit("No active private read-only approval; installation refused")
PY
    mkdir -p -m 700 "${UNIT_DIR}"
    [[ ! -e "${UNIT_DIR}/${NAME}.service" &&
       ! -e "${UNIT_DIR}/${NAME}.timer" ]] || {
      printf 'Timer exists; will not overwrite its schedule\n' >&2; exit 2;
    }
    umask 077
    cat > "${UNIT_DIR}/${NAME}.service" <<'UNIT'
[Unit]
Description=Redux approved read-only physical acceptance observation

[Service]
Type=oneshot
WorkingDirectory=%h/redux-workbench-src
ExecStart=/usr/bin/python3 -B %h/redux-workbench-src/scripts/redux-field-bridge.py observe
TimeoutStartSec=100s
NoNewPrivileges=yes
UMask=0077
UNIT
    cat > "${UNIT_DIR}/${NAME}.timer" <<'UNIT'
[Unit]
Description=Redux gated read-only Pi inventory hourly

[Timer]
OnCalendar=*-*-* *:20:00
Persistent=true
RandomizedDelaySec=5min
Unit=redux-field-bridge.service

[Install]
WantedBy=timers.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable --now "${NAME}.timer"
    printf 'Separate read-only Pi schedule enabled; limited by private approval.\n'
    printf 'Revoke/disable: bash scripts/install-redux-field-bridge.sh --disable\n'
    ;;
  --status)
    # No SSH, no private policy disclosure, and no stale report promoted
    # to live physical acceptance. This inspects only local metadata.
    python3 "${BASE}/scripts/redux-field-doctor.py" --json
    ;;
  --disable)
    systemctl --user disable --now "${NAME}.timer" 2>/dev/null || true
    systemctl --user stop "${NAME}.service" 2>/dev/null || true
    python3 "${BASE}/scripts/redux-field-bridge.py" revoke || true
    printf 'Read-only Pi schedule disabled and recurring approval revoked.\n'
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
