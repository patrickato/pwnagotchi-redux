#!/usr/bin/env python3
"""Bounded, read-only Raspberry Pi development report (no RF changes).

Only --save writes a single, root-only latest snapshot on REDUXCAP. Raw
captures, caplet files, and passwords are NEVER included in the report.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.error
import urllib.request

from redux.storage_mount import capture_mount_snapshot

STATE = Path("/captures/live/live.json")
ENGINE_LOG = Path("/captures/live/bettercap.log")
SAVED = Path("/captures/diagnostics/latest.txt")
ALLOWED = frozenset(("redux.service", "redux-live.service",
                    "redux-capture-ingest.timer"))
MAC = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
CONTROL = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def clean(line: str) -> str:
    """Defang tokens and RF identifiers before text leaves the card."""
    line = CONTROL.sub("", line)
    line = MAC.sub("[MAC]", line)
    line = re.sub(r"(?i)(api\.rest\.password\s*[=: ]\s*)[^\s]+",
                  r"\1[REDACTED]", line)
    line = re.sub(r"(?i)(authorization\s*:\s*basic\s+)\S+",
                  r"\1[REDACTED]", line)
    line = re.sub(r"(?i)(password\s*[=:]\s*)\S+",
                  r"\1[REDACTED]", line)
    return line[:250]


def command(args, *, limit=8) -> list[str]:
    try:
        run = subprocess.run(args, capture_output=True, text=True,
                             timeout=4, check=False)
        lines = (run.stdout + ("\n" + run.stderr if run.stderr else "")).splitlines()
        return [clean(line) for line in lines[:limit]] or ["(no output)"]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"unavailable: {type(exc).__name__}"]


def read_json(path=STATE) -> dict:
    try:
        if path.stat().st_size > 131072 or path.is_symlink():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def capture_link_type(iface, *, net_root=Path("/sys/class/net")):
    """Read the selected netdev's real packet link type, without radio changes."""
    if (not isinstance(iface, str) or iface in (".", "..")
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", iface)):
        return "unobserved"
    try:
        value = (net_root / iface / "type").read_text(encoding="ascii").strip()
        if not value.isdecimal() or len(value) > 5 or int(value) > 65535:
            return "unobserved"
        return int(value)
    except (OSError, UnicodeError):
        return "unobserved"


def log_tail(path=ENGINE_LOG, *, count=14) -> list[str]:
    try:
        if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
            return ["(engine log unavailable or exceeds limit)"]
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 16384))
            raw = stream.read(16384).decode("utf-8", "replace")
        return [clean(line) for line in raw.splitlines()[-count:]] or ["(empty)"]
    except OSError:
        return ["(engine log not yet available)"]


def api_status(*, opener=None) -> str:
    """Unauthenticated loopback probe; never reads session data or credentials.

    Bettercap v2.32.0 responds 401 to GET /api/session when authenticated,
    200 if authentication is disabled. HTTP 400 is unexpected for this route.
    Ignore inherited HTTP proxy configuration to preserve probe provenance.
    """
    client = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request("http://127.0.0.1:8081/api/session", method="GET")
    try:
        with client.open(req, timeout=1.0) as response:
            if response.status == 200:
                return "HTTP 200 (warning: API appears unauthenticated)"
            return f"HTTP {response.status} (unexpected)"
    except urllib.error.HTTPError as error:
        if error.code == 401:
            return "HTTP 401 (authentication required; API responding)"
        return f"HTTP {error.code} (unexpected for GET /api/session)"
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        return f"unavailable ({type(error).__name__})"


def snapshot(*, full=False) -> str:
    d = read_json()
    lines = [
        "REDUX FIELD REPORT (passive, no wireless changes)",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "captures_ext4: " + str(capture_mount_snapshot() is not None),
    ]
    for unit in sorted(ALLOWED):
        lines.append(f"{unit}: {command(['systemctl', 'is-active', unit], limit=1)[0]}")
    for name, key in (("live_state", "state"), ("capture_radio", "iface"),
                      ("last_error", "last_error"),
                      ("dashboard", "dashboard_active"),
                      ("engine_attempts", "engine_attempts"),
                      ("failure_streak", "failure_streak"),
                      ("retry_delay_seconds", "retry_delay_seconds"),
                      ("retry_in_seconds", "retry_in_seconds"),
                      ("events_seen", "events_seen"),
                      ("handshake_events", "handshake_events"),
                      ("handed_off", "handed_off")):
        lines.append(f"{name}: {clean(str(d.get(key, 'unobserved')))}")
    lines.append("capture_link_type: " + str(capture_link_type(d.get("iface"))))
    lines.append("bettercap_api_probe: " + api_status())
    if full:
        lines.append("== RF kill ==")
        lines.extend(command(["rfkill", "list"], limit=48))
        lines.append("== Regulatory domain ==")
        lines.extend(command(["iw", "reg", "get"], limit=10))
        lines.append("== Interfaces ==")
        lines.extend(command(["iw", "dev"], limit=48))
    lines.append("== Bettercap last output (sanitized; no caplet) ==")
    lines.extend(log_tail(count=14 if full else 8))
    return "\n".join(lines) + "\n"


def save(report, *, destination=SAVED, mount_check=capture_mount_snapshot):
    if mount_check() is None:
        raise RuntimeError("REDUXCAP ext4 mount unavailable; refusing to save on rootfs")
    parent = destination.parent
    if parent.is_symlink():
        raise ValueError("diagnostics directory cannot be a symlink")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink() or (destination.exists() and destination.is_symlink()):
        raise ValueError("diagnostics path cannot be a symlink")
    os.chmod(parent, 0o700)
    fd, temp = tempfile.mkstemp(dir=parent, prefix=".report-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(report[:24000])
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, destination)
        os.chmod(destination, 0o600)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="include RF inventory and log tail")
    parser.add_argument("--save", action="store_true", help="save private latest report on REDUXCAP")
    args = parser.parse_args(argv)
    report = snapshot(full=args.full)
    if args.save:
        save(report)
        print("Saved local diagnostic snapshot to /captures/diagnostics/latest.txt")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
