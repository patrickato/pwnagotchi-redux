#!/usr/bin/env bash
# Optional user-owned 6-hour timer for source-only local Codex engineering.
# Does not use sudo, touch Pi/SD/network configurations, or add credentials.
set -euo pipefail
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
UNIT_DIR="${HOME}/.config/systemd/user"
NAME="redux-autopilot"

usage() {
  printf 'Usage: bash scripts/install-redux-autopilot.sh --plan|--install|--status|--disable\n'
}

case "${1:---plan}" in
  --plan)
    printf 'No changes. Optional Codex source-only user timer, every 6 hours.\n'
    printf 'No Pi SSH, sudo, device controls or releases.\n'
    printf 'WSL and its user systemd manager must be running for scheduled jobs.\n'
    printf 'First run: python3 scripts/redux-autopilot.py plan\n'
    ;;
  --install)
    [[ "${BASE}" == "${HOME}/redux-workbench-src" ]] || {
      printf 'Refusing: expected trusted Ubuntu checkout in ~/redux-workbench-src\n' >&2; exit 2;
    }
    [[ "$(git -C "${BASE}" branch --show-current)" == "human/redux-workbench" ]] || {
      printf 'Refusing: incorrect workbench branch\n' >&2; exit 2;
    }
    [[ -z "$(git -C "${BASE}" status --porcelain)" ]] || {
      printf 'Refusing: dirty workbench; resolve local changes first\n' >&2; exit 2;
    }
    command -v codex >/dev/null || {
      printf 'Refusing: Codex CLI not installed/visible in Ubuntu WSL\n' >&2; exit 2;
    }
    command -v python3 >/dev/null || exit 2
    command -v git >/dev/null || exit 2
    # User-systemd typically has a minimal PATH. Carry only Codex/Node's
    # bin directory, not ambient shell exports or secret environment data.
    CODEX_BIN="$(command -v codex)"
    [[ "${CODEX_BIN}" =~ ^/[A-Za-z0-9_./+-]+$ ]] || {
      printf 'Refusing: Codex executable path cannot be safely pinned\n' >&2; exit 2;
    }
    CODEX_DIR="$(dirname -- "${CODEX_BIN}")"
    systemctl --user show-environment >/dev/null 2>&1 || {
      printf 'Refusing: systemd user manager unavailable in this WSL session\n' >&2; exit 2;
    }
    python3 "${BASE}/scripts/redux-autopilot.py" plan
    # Never enable a timer whose guardrail regressions are failing.
    python3 -m pytest -q "${BASE}/tests/test_redux_autopilot.py" || {
      printf 'Refusing: autopilot guardrail tests failed\n' >&2; exit 2;
    }
    mkdir -p -m 700 "${UNIT_DIR}"
    if [[ -e "${UNIT_DIR}/${NAME}.service" || -e "${UNIT_DIR}/${NAME}.timer" ]]; then
      printf 'Refusing to overwrite existing timer/service files; inspect them manually\n' >&2
      exit 2
    fi
    umask 077
    cat > "${UNIT_DIR}/${NAME}.service" <<'UNIT'
[Unit]
Description=Redux source-only Codex engineering cycle

[Service]
Type=oneshot
WorkingDirectory=%h/redux-workbench-src
ExecStart=/usr/bin/python3 %h/redux-workbench-src/scripts/redux-autopilot.py cycle --enable-agent
TimeoutStartSec=100min
NoNewPrivileges=yes
UNIT
    printf 'Environment=PATH=%s:%%h/.local/bin:/usr/local/bin:/usr/bin:/bin\n' "${CODEX_DIR}" >> "${UNIT_DIR}/${NAME}.service"
    cat > "${UNIT_DIR}/${NAME}.timer" <<'UNIT'
[Unit]
Description=Run Redux source-only Codex cycle every six hours

[Timer]
OnCalendar=*-*-* 01,07,13,19:00:00
Persistent=true
RandomizedDelaySec=5min
Unit=redux-autopilot.service

[Install]
WantedBy=timers.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable --now "${NAME}.timer"
    printf 'Enabled; source-only Codex runs while Ubuntu WSL + user systemd run.\n'
    printf 'Status: systemctl --user list-timers redux-autopilot.timer\n'
    printf 'Disable: bash scripts/install-redux-autopilot.sh --disable\n'
    ;;
  --status)
    systemctl --user list-timers "${NAME}.timer" || true
    if [[ -f "${HOME}/.local/state/redux-autopilot/last.json" ]]; then
      cat "${HOME}/.local/state/redux-autopilot/last.json"
    fi
    ;;
  --disable)
    systemctl --user disable --now "${NAME}.timer" 2>/dev/null || true
    printf 'Disabled scheduled Codex cycles; no Pi operation affected.\n'
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
