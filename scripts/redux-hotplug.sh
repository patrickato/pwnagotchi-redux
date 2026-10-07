#!/bin/sh
# redux-hotplug.sh — bridge udev add/remove events into the Radio Orchestrator.
#
# Called by 90-redux-radio.rules as:  redux-hotplug.sh <add|remove> <iface>
#
# It does the minimum in shell (udev's environment is hostile: no PATH, no TTY, killed if
# it blocks) and hands off to the Python orchestrator, which owns the actual decision and
# the iw/ip mode changes. The orchestrator re-probes, recomputes roles, and applies the
# *minimal* set of interface changes (see redux/radio/manager.py plan_transition).
#
# Scope: this only triggers monitor/managed/down mode changes for role assignment. It never
# deauths, injects, or targets anything.
set -eu

ACTION="${1:-}"
IFACE="${2:-}"
REDUX_HOME="${REDUX_HOME:-/opt/redux}"
PYTHON="${REDUX_PYTHON:-/usr/bin/python3}"
LOG="${REDUX_LOG:-/var/log/redux/radio.log}"

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

log() { printf '%s hotplug %s %s %s\n' "$(date -Is 2>/dev/null || date)" "$ACTION" "$IFACE" "$*" >> "$LOG" 2>/dev/null || true; }

if [ -z "$ACTION" ] || [ -z "$IFACE" ]; then
    log "missing args (need: <add|remove> <iface>)"
    exit 2
fi

case "$ACTION" in
    add|remove) : ;;
    *) log "unknown action"; exit 2 ;;
esac

log "dispatching to orchestrator"

# Hand off. The CLI entrypoint is responsible for re-probing and applying mode changes;
# it is idempotent, so a duplicate udev event is harmless.
exec "$PYTHON" -m redux.radio.hotplug "$ACTION" "$IFACE" \
    >> "$LOG" 2>&1
