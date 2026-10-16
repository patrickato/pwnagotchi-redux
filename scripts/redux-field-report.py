#!/usr/bin/env python3
"""Bounded, read-only Raspberry Pi development report (no RF changes).

Only --save writes a single, root-only latest snapshot on REDUXCAP. Raw
captures, caplet files, and passwords are NEVER included in the report.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.request

from redux.storage_mount import capture_mount_snapshot
from redux.core.regulatory import current_country, wireless_unblocked
from redux.crack.ingest import read_summary
from redux.radio.link_type import read_link_type

STATE = Path("/captures/live/live.json")
ENGINE_LOG = Path("/captures/live/bettercap.log")
SAVED = Path("/captures/diagnostics/latest.txt")
ALLOWED = frozenset(("redux.service", "redux-live.service",
                    "redux-capture-ingest.timer"))
MAC = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
CONTROL = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CHECKPOINT_STALE_SECONDS = 12


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


def bounded_bytes(path, limit):
    """Pin a regular, nofollow file and bound the actual read, not just stat."""
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return None
        with os.fdopen(fd, "rb") as stream:
            fd = None
            data = stream.read(limit + 1)
        return data if len(data) <= limit else None
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def read_json(path=STATE) -> dict:
    try:
        raw = bounded_bytes(path, 131072)
        if raw is None:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeError):
        return {}


def capture_link_type(iface, *, net_root=Path("/sys/class/net")):
    """Read the selected netdev's real packet link type, without radio changes."""
    value = read_link_type(iface, sys_root=net_root)
    return value if value is not None else "unobserved"


def radio_ownership(iface, *, socket_root=Path("/run/wpa_supplicant")):
    """Report observed manager claims, never infer exclusive radio ownership."""
    def active(unit):
        value = command(["systemctl", "is-active", unit], limit=1)[0]
        return True if value == "active" else False if value in {"inactive", "failed"} else None
    result = {"networkmanager_active": active("NetworkManager.service"),
              "wpa_supplicant_active": active("wpa_supplicant.service"),
              "nm_managed": None, "nm_state_code": None,
              "supplicant_interface_socket": None}
    if (not isinstance(iface, str) or iface in (".", "..")
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", iface)):
        return result
    lines = command(["nmcli", "-g", "GENERAL.STATE,GENERAL.NM-MANAGED",
                     "device", "show", iface], limit=2)
    state = re.fullmatch(r"([0-9]{1,3})(?: \([^\n]*\))?", lines[0])
    if state and int(state.group(1)) <= 120:
        result["nm_state_code"] = int(state.group(1))
    if len(lines) > 1 and lines[1] in {"yes", "no"}:
        result["nm_managed"] = lines[1] == "yes"
    try:
        metadata = (socket_root / iface).lstat()
        result["supplicant_interface_socket"] = stat.S_ISSOCK(metadata.st_mode)
    except FileNotFoundError:
        result["supplicant_interface_socket"] = False
    except OSError:
        pass
    return result


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
    checkpoint = checkpoint_health(d.get("updated_utc"))
    lines = [
        "REDUX FIELD REPORT (passive, no wireless changes)",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "captures_ext4: " + str(capture_mount_snapshot() is not None),
        "checkpoint_freshness: " + checkpoint["status"],
        "checkpoint_age_seconds: " + str(checkpoint["age_seconds"]),
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


def number(value):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def checkpoint_health(updated_utc, *, now=None):
    """A saved 'running' label cannot establish that the supervisor is live."""
    result = {"status": "unobserved", "age_seconds": None,
              "stale_seconds": CHECKPOINT_STALE_SECONDS}
    if updated_utc is None:
        return result
    if number(updated_utc) is None:
        return {**result, "status": "invalid"}
    current = time.time() if now is None else now
    if number(current) is None:
        return {**result, "status": "invalid"}
    age = current - updated_utc
    if age < 0:
        return {**result, "status": "future"}
    return {**result, "status": "fresh" if age <= CHECKPOINT_STALE_SECONDS else "stale",
            "age_seconds": round(age, 1)}


def capture_inventory(directory, *, limit=100):
    """Names and sizes only, from a pinned directory; never read capture bytes."""
    result = {"available": False, "files": [], "truncated": False}
    fd = None
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        with os.scandir(fd) as entries:
            for index, entry in enumerate(entries):
                if index >= limit:
                    result["truncated"] = True
                    break
                st = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(st.st_mode) and entry.name.endswith(".pcap"):
                    # Operator-supplied filenames may embed SSIDs/MACs.
                    name = (entry.name if re.fullmatch(r"bettercap-[0-9a-f]{16}\.pcap", entry.name)
                            else "[filename withheld]")
                    result["files"].append({"name": name, "bytes": st.st_size})
        result["available"] = True
    except OSError:
        pass  # Partial inventory remains explicitly unavailable.
    finally:
        if fd is not None:
            os.close(fd)
    return result


def snapshot_summary(*, config=Path("/etc/redux/live.toml")):
    """Allowlisted observations only: no raw logs, RF IDs or free-text errors."""
    d = read_json()
    checkpoint = checkpoint_health(d.get("updated_utc"))
    states = {"running", "degraded", "starting", "starting_engine", "rotating",
              "storage_paused", "telemetry_degraded", "termination_pending", "stopped"}
    state = d.get("state")
    runtime = {"state": state if isinstance(state, str) and state in states else None}
    for key in ("engine_attempts", "failure_streak", "retry_in_seconds", "events_seen",
                "handshake_events", "handed_off", "updated_utc"):
        runtime[key] = number(d.get(key))
    known_errors = {
        "selected regulatory country is not active": "regulatory_country_inactive",
        "Wi-Fi rfkill is blocked or could not be verified": "rfkill_unverified_or_blocked",
        "radio mode change failed": "radio_mode_change_failed",
        "no monitor-capable Wi-Fi interface detected": "capture_interface_missing",
    }
    error = d.get("last_error")
    runtime["error"] = (next((value for key, value in known_errors.items()
                             if isinstance(error, str) and key in error), "unclassified")
                        if error else None)
    for key in ("handoff_error", "sightings_write_error", "log_error"):
        runtime[key + "_present"] = bool(d.get(key))
    iface = d.get("iface")
    link = capture_link_type(iface)
    # Report an interface only when its sysfs measurement was obtained.
    iface = iface if type(link) is int else None
    radio = {"iface": iface, "link_type": link if type(link) is int else None,
             "mode": None, "channel": None}
    if iface:
        info = "\n".join(command(["iw", "dev", iface, "info"], limit=16))
        mode = re.search(r"(?m)^\s*type (managed|monitor)\s*$", info)
        channel = re.search(r"\bchannel (\d+)\b", info)
        radio.update(mode=mode.group(1) if mode else None,
                     channel=int(channel.group(1)) if channel else None)
    reg = "\n".join(command(["iw", "reg", "get"], limit=48))
    rfkill = "\n".join(command(["rfkill", "list"], limit=48))
    blocks = [block for block in re.split(r"(?m)^\d+:\s*", rfkill) if "Wireless LAN" in block]
    rf_complete = bool(blocks) and all(
        re.search(r"Soft blocked:\s*(yes|no)\b", block, re.I)
        and re.search(r"Hard blocked:\s*(yes|no)\b", block, re.I) for block in blocks)
    rotation = country = None
    raw = bounded_bytes(config, 65536)
    try:
        values = tomllib.loads(raw.decode("utf-8")).get("live", {}) if raw else {}
        rotation = number(values.get("rotation_seconds"))
    except (ValueError, UnicodeError, AttributeError):
        pass
    # The baked country file is independent of live.toml.
    raw = bounded_bytes(Path("/etc/redux/regulatory-country"), 8)
    if raw:
        candidate = raw.decode("ascii", "replace").strip()
        country = candidate if re.fullmatch(r"[A-Z]{2}", candidate) else None
    processing = read_summary("/captures/jobs.db")
    scan = processing.get("last_scan") or {}
    statuses = {"ready", "invalid", "error", "duplicate", "deferred", "skipped",
                "deferred_low_storage", "paused_low_storage", "storage_error"}
    def counts(values):
        return ({key: number(value) for key, value in values.items() if key in statuses}
                if isinstance(values, dict) else {})
    api = api_status()
    http = re.match(r"HTTP (\d{3})\b", api)
    return {
        "schema": 1, "summary_only": True,
        "sampled_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "captures_ext4": capture_mount_snapshot() is not None,
        "services": {unit: (value if value in {"active", "inactive", "failed", "activating", "deactivating"}
                            else "unobserved")
                     for unit in sorted(ALLOWED)
                     for value in [command(["systemctl", "is-active", unit], limit=1)[0]]},
        "runtime": runtime, "checkpoint": checkpoint, "radio": radio,
        "radio_ownership": radio_ownership(iface), "rotation_seconds": rotation,
        "regulatory": {"configured_country": country, "global_country": current_country(reg) or None,
                       "wifi_unblocked": wireless_unblocked(rfkill) if rf_complete else None},
        "bettercap_http": int(http.group(1)) if http else None,
        "captures": {name: capture_inventory(Path("/captures") / name) for name in ("active", "incoming")},
        "processing": {"available": bool(processing.get("available")),
                       "artifacts": number(processing.get("artifacts")),
                       "by_status": counts(processing.get("by_status", {})),
                       "last_scan_utc": number(scan.get("completed_utc")),
                       "scanned": number(scan.get("scanned")),
                       "outcomes": counts(scan.get("outcomes", {})),
                       "scan_error_present": bool(processing.get("scan_error"))},
    }


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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true", help="include RF inventory and log tail")
    mode.add_argument("--summary", action="store_true", help="allowlisted JSON; no raw logs or RF identifiers")
    parser.add_argument("--save", action="store_true", help="save private latest report on REDUXCAP")
    args = parser.parse_args(argv)
    report = (json.dumps(snapshot_summary(), sort_keys=True, allow_nan=False) + "\n"
              if args.summary else snapshot(full=args.full))
    if args.save:
        save(report)
        print("Saved local diagnostic snapshot to /captures/diagnostics/latest.txt")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
