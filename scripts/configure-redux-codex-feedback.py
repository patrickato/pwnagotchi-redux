#!/usr/bin/env python3
"""Explicit local opt-in for privacy-filtered Pi observations in Codex prompts.

DOES NOT contact the Pi, read private hardware reports, modify the field
observer, or run Codex. Default --plan is strictly offline. Enabling sends
ONLY schema-validated categorical metadata to Codex cloud on future runs;
the operator must separately consent to that data transfer.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

SERVICE = Path.home() / ".config/systemd/user/redux-autopilot.service"
EXPECTED = ("ExecStart=/usr/bin/python3 "
            "%h/redux-workbench-src/scripts/redux-autopilot.py cycle --enable-agent")
WITH_FEEDBACK = EXPECTED + " --with-sanitized-field-feedback"
FORBIDDEN = ("ExecStartPre=", "ExecStartPost=", "ExecStop=", "ExecStopPost=")
MAX_BYTES = 8192


def private_unit_text(path=SERVICE):
    """Require precisely the user-installed source-only service semantics."""
    unit = Path(path)
    fd = os.open(unit, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or not 0 < before.st_size <= MAX_BYTES or before.st_nlink != 1):
            raise ValueError("untrusted_codex_service")
        data = os.read(fd, MAX_BYTES + 1)
        after = os.fstat(fd)
        if (len(data) > MAX_BYTES or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise ValueError("unstable_codex_service")
    finally:
        os.close(fd)
    try:
        content = data.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("unreadable_codex_service") from exc
    lines = content.splitlines()
    if (lines.count("Type=oneshot") != 1
            or lines.count("WorkingDirectory=%h/redux-workbench-src") != 1
            or lines.count("NoNewPrivileges=yes") != 1
            or len([x for x in lines if x.startswith("ExecStart=")]) != 1
            or any(any(x.startswith(p) for p in FORBIDDEN) for x in lines)):
        raise ValueError("unrecognized_codex_service")
    current = "on" if WITH_FEEDBACK in lines else "off" if EXPECTED in lines else None
    if current is None:
        raise ValueError("unrecognized_codex_service")
    return content, current


def atomic_update(path, value):
    """Create a user-private replacement, never follow a named destination link."""
    unit = Path(path)
    directory = unit.parent
    parent = directory.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()):
        raise ValueError("untrusted_unit_directory")
    # Recheck unit identity before writing; never overwrite a changed unit.
    private_unit_text(unit)
    descriptor, name = tempfile.mkstemp(prefix=".redux-codex-feedback-", dir=directory)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            out.write(value)
            out.flush()
            os.fsync(out.fileno())
        # Protect against ordinary concurrent alterations between validation
        # and replacement. Operator owns this local per-user service.
        private_unit_text(unit)
        os.replace(name, unit)
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def reload_systemd(*, runner=subprocess.run):
    try:
        result = runner(["systemctl", "--user", "daemon-reload"],
                        capture_output=True, text=True, timeout=12, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def configure(*, enable, acknowledged, service=None,
              reload=reload_systemd):
    if enable and not acknowledged:
        raise ValueError("explicit_sanitized_model_consent_required")
    unit = SERVICE if service is None else Path(service)
    content, current = private_unit_text(unit)
    requested = "on" if enable else "off"
    if current == requested:
        return {"schema": 1, "status": "already_configured",
                "sanitized_model_feedback": requested,
                "pi_contact": False, "field_approval_changed": False}
    old = EXPECTED if current == "off" else WITH_FEEDBACK
    new = WITH_FEEDBACK if enable else EXPECTED
    if content.splitlines().count(old) != 1:
        raise ValueError("ambiguous_codex_command")
    replacement = content.replace(old, new, 1)
    atomic_update(unit, replacement)
    if not reload():
        atomic_update(unit, content)
        if not reload():
            raise ValueError("systemd_rollback_unverified")
        raise ValueError("systemd_reload_failed")
    return {"schema": 1, "status": "configured",
            "sanitized_model_feedback": requested,
            "pi_contact": False, "field_approval_changed": False,
            "service_reload_only": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--plan", action="store_true")
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--enable", action="store_true")
    actions.add_argument("--disable", action="store_true")
    parser.add_argument("--approve-sanitized-model-data", action="store_true",
                        help="Explicit consent to submit categorical Pi metadata to Codex")
    args = parser.parse_args(argv)
    if args.plan:
        print(json.dumps({
            "schema": 1, "mode": "optional_sanitized_codex_feedback",
            "enabled_by_default": False,
            "requires_explicit_model_data_consent": True,
            "raw_capture_transfer": False, "ssh_key_transfer": False,
            "ssid_mac_ip_transfer": False,
            "pi_contact": False, "field_approval_changed": False,
            "local_systemd_user_service_change_only": True,
        }, sort_keys=True))
        return 0
    try:
        if args.status:
            _, mode = private_unit_text()
            outcome = {"schema": 1, "status": "ok",
                       "sanitized_model_feedback": mode, "pi_contact": False}
        else:
            outcome = configure(enable=args.enable,
                                acknowledged=args.approve_sanitized_model_data)
        print(json.dumps(outcome, sort_keys=True))
        return 0
    except (ValueError, OSError):
        print(json.dumps({"schema": 1, "status": "blocked",
                          "reason": "local_consent_or_unit_guard_failed",
                          "pi_contact": False}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
