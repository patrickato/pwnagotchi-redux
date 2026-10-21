#!/usr/bin/env python3
"""Offline health of the already approved, locally frozen Redux Pi observer.

Reads local private status and systemd metadata, NEVER contacts Pi or
consumes an approved collection. No credentials, addresses, SSIDs, MACs,
paths, raw hardware reports, subprocess stderr or journal contents leak.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

HOME = Path.home()
STATE = HOME / ".local/state/redux-field-bridge"
SERVICE = HOME / ".config/systemd/user/redux-field-bridge.service"
TIMER = HOME / ".config/systemd/user/redux-field-bridge.timer"
APPROVAL = "recurring-approval.json"
LAST = "last-status.json"
SCHEMA = 1
STATES = frozenset(("observed", "unavailable", "expired", "rate_limited",
                     "exhausted", "source_changed", "revoked", "not_enrolled"))
RESULTS = frozenset(("success", "exit-code", "timeout", "signal",
                     "core-dump", "resources", "protocol", "watchdog",
                     "start-limit-hit"))
ACTIVE = frozenset(("active", "inactive", "failed", "activating", "deactivating"))
EXPECTED_SERVICE = (
    "ExecStart=/usr/bin/python3 -B "
    "%h/.local/share/redux-field-bridge/approved-worktree/"
    "scripts/redux-field-bridge.py observe"
)
EXPECTED_WORKDIR = (
    "WorkingDirectory=%h/.local/share/redux-field-bridge/approved-worktree"
)
EXPECTED_TIMER = "OnCalendar=*-*-* *:20:00"
FROZEN = HOME / ".local/share/redux-field-bridge/approved-worktree"


def owner_file(path, *, limit=8192):
    """Bounded no-follow private regular file; never expose source bytes."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or not 0 < before.st_size <= limit
                or before.st_nlink != 1):
            raise ValueError("untrusted_private_evidence")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        if (len(raw) > limit or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise ValueError("unstable_private_evidence")
        return raw
    finally:
        os.close(fd)


def strict_json(raw):
    def unique(items):
        output = {}
        for key, value in items:
            if key in output:
                raise ValueError("duplicate_private_json_key")
            output[key] = value
        return output
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(
                          ValueError("nonfinite_private_json")))


def private_directory(path):
    meta = path.lstat()
    if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid()
            or stat.S_IMODE(meta.st_mode) != 0o700):
        raise ValueError("untrusted_private_state")


def approved_summary(state, now):
    try:
        private_directory(state)
        policy = strict_json(owner_file(state / APPROVAL))
        expected = {"schema", "host", "key_path", "created", "expires",
                    "interval_seconds", "source_revision", "launcher_sha256",
                    "remote_bundle_sha256", "max_sessions", "used",
                    "last_attempt", "enabled"}
        if type(policy) is not dict or set(policy) != expected or policy["schema"] != 1:
            raise ValueError("untrusted_approval_shape")
        for k in ("max_sessions", "used", "created", "expires", "interval_seconds"):
            if type(policy[k]) is not int:
                raise ValueError("untrusted_approval_count")
        if (not 0 <= policy["used"] <= policy["max_sessions"] <= 40
                or not 0 < policy["created"] < policy["expires"]
                or policy["expires"] - policy["created"] > 14 * 86400
                or policy["interval_seconds"] < 3600
                or type(policy["enabled"]) is not bool):
            raise ValueError("untrusted_approval_bounds")
        last_attempt = policy["last_attempt"]
        if (last_attempt is not None and (type(last_attempt) is not int
                or last_attempt < policy["created"])):
            raise ValueError("untrusted_approval_attempt")
        if now < policy["created"]:
            status = "clock_before_approval"
        elif not policy["enabled"]:
            status = "revoked"
        elif now >= policy["expires"]:
            status = "expired"
        elif policy["used"] >= policy["max_sessions"]:
            status = "exhausted"
        else:
            status = "eligible_without_source_attestation"
        return {
            "status": status, "sessions_used": policy["used"],
            "sessions_remaining": policy["max_sessions"] - policy["used"],
            "interval_minutes": policy["interval_seconds"] // 60,
            "expiry_hours": max(0, (policy["expires"] - now) // 3600),
            "last_attempt_minutes_ago": (max(0, (now - last_attempt) // 60)
                                         if last_attempt is not None else None),
        }
    except FileNotFoundError:
        return {"status": "no_approval"}
    except (ValueError, OSError, TypeError, KeyError, UnicodeError):
        return {"status": "unavailable"}


def last_summary(state):
    try:
        private_directory(state)
        result = strict_json(owner_file(state / LAST))
        if (type(result) is not dict or
                set(result) != {"schema", "status", "performed",
                                "physical_acceptance", "sessions_used",
                                "sessions_remaining", "report_saved_private",
                                "no_pi_changes_requested"} or
                result["schema"] != 1 or result["status"] not in ("observed", "unavailable")
                or result["performed"] is not True
                or result["physical_acceptance"] != "UNKNOWN"
                or result["no_pi_changes_requested"] is not True
                or type(result["report_saved_private"]) is not bool
                or any(type(result[k]) is not int or result[k] < 0 or result[k] > 40
                       for k in ("sessions_used", "sessions_remaining"))):
            raise ValueError("untrusted_last_status")
        return {"status": result["status"], "attempted": True,
                "private_report_saved": result["report_saved_private"],
                "sessions_used_at_attempt": result["sessions_used"]}
    except FileNotFoundError:
        return {"status": "never_recorded"}
    except (ValueError, OSError, KeyError, TypeError):
        return {"status": "unavailable"}


def trusted_unit(path, expected):
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_size > 8192 or info.st_nlink != 1):
            return "unavailable"
        content = path.read_text("utf-8")
        if (content.count(expected) != 1 or
                content.count("ExecStart=") != (1 if "ExecStart=" in expected else 0)
                and "ExecStart=" in expected):
            return "unrecognized"
        return "expected"
    except (OSError, UnicodeError):
        return "unavailable"


def service_metadata(*, runner=subprocess.run):
    # A single bounded, noninteractive *local* systemctl query; never journal
    # disclosure, never starts/stops/touches even a user service.
    try:
        response = runner(
            ["systemctl", "--user", "show", "redux-field-bridge.service",
             "--property=ActiveState", "--property=Result",
             "--property=ExecMainStatus", "--property=ExecMainCode"],
            capture_output=True, text=True, timeout=8, check=False)
        if response.returncode != 0 or len(response.stdout) > 2048:
            raise ValueError("systemctl_unavailable")
        values = {}
        for line in response.stdout.splitlines():
            if "=" not in line:
                raise ValueError("malformed_systemd_metadata")
            key, value = line.split("=", 1)
            if key in values or key not in {"ActiveState", "Result",
                                             "ExecMainStatus", "ExecMainCode"}:
                raise ValueError("unexpected_systemd_metadata")
            values[key] = value
        if set(values) != {"ActiveState", "Result", "ExecMainStatus", "ExecMainCode"}:
            raise ValueError("incomplete_systemd_metadata")
        return {
            "active_state": (values["ActiveState"] if values["ActiveState"] in ACTIVE else None),
            "last_result": (values["Result"] if values["Result"] in RESULTS else "unknown"),
            "exit_code": (int(values["ExecMainStatus"])
                          if re.fullmatch(r"[0-9]{1,3}", values["ExecMainStatus"])
                          and int(values["ExecMainStatus"]) <= 255 else None),
        }
    except (OSError, ValueError, subprocess.TimeoutExpired, TypeError):
        return {"active_state": None, "last_result": "unavailable",
                "exit_code": None}


def inspect(*, state=STATE, service=SERVICE, timer=TIMER, now=None,
            runner=subprocess.run):
    clock = int(time.time()) if now is None else int(now)
    svc = trusted_unit(service, EXPECTED_SERVICE)
    workdir = trusted_unit(service, EXPECTED_WORKDIR)
    tm = trusted_unit(timer, EXPECTED_TIMER)
    frozen_state = "present" if FROZEN.is_dir() and not FROZEN.is_symlink() else "unknown"
    return {"schema": 1, "format": "redux-offline-field-doctor-v1",
            "network_actions_performed": False,
            "pi_configuration_changes": False,
            "physical_acceptance": "UNKNOWN",
            "approved": approved_summary(state, clock),
            "last_recorded_collection": last_summary(state),
            "unit": {"command": svc, "working_directory": workdir,
                     "hourly_timer": tm, "frozen_checkout": frozen_state,
                     "systemd": service_metadata(runner=runner)}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", required=True,
                        help="Emit bounded redacted offline health")
    parser.parse_args(argv)
    print(json.dumps(inspect(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
