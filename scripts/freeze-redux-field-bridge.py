#!/usr/bin/env python3
"""One-time LOCAL migration of an approved Pi observer to immutable Git worktree.

The field timer already has a separate time-limited, read-only approval.
This tool does not initiate SSH or extend that approval. It pins the *old*
approved Git revision, verifies the unchanged collector bundle, and changes
only the installed user-systemd service's ExecStart path. Default is plan.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / ".local" / "state" / "redux-field-bridge"
FREEZE = Path.home() / ".local" / "share" / "redux-field-bridge" / "approved-worktree"
SERVICE = Path.home() / ".config" / "systemd" / "user" / "redux-field-bridge.service"
TIMER = Path.home() / ".config" / "systemd" / "user" / "redux-field-bridge.timer"
OLD_EXEC = "ExecStart=/usr/bin/python3 -B %h/redux-workbench-src/scripts/redux-field-bridge.py observe"
NEW_EXEC = ("ExecStart=/usr/bin/python3 -B "
            "%h/.local/share/redux-field-bridge/approved-worktree/"
            "scripts/redux-field-bridge.py observe")
OLD_WORKDIR = "WorkingDirectory=%h/redux-workbench-src"
NEW_WORKDIR = ("WorkingDirectory=%h/.local/share/redux-field-bridge/"
               "approved-worktree")
SHA = re.compile(r"[a-f0-9]{40}\Z")


def command(argv, *, cwd=None, deadline=20):
    try:
        return subprocess.run(argv, cwd=cwd, text=True, capture_output=True,
                              check=False, timeout=deadline)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("local_command_unavailable") from exc


def strict_regular(file, mode=None, size=32768):
    p = Path(file)
    metadata = p.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ValueError("untrusted_local_file")
    if mode is not None and stat.S_IMODE(metadata.st_mode) != mode:
        raise ValueError("untrusted_local_permissions")
    if metadata.st_size > size:
        raise ValueError("oversized_local_file")
    return p


def module_at(root):
    path = strict_regular(root / "scripts" / "redux-field-bridge.py", size=128*1024)
    spec = importlib.util.spec_from_file_location("redux_frozen_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def approval():
    root = STATE
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("untrusted_local_state")
    policy = strict_regular(root / "recurring-approval.json", mode=0o600, size=8192)
    # Do not hand-parse a policy with a weaker contract than the bridge.
    bridge = module_at(ROOT)
    value = bridge.load_policy()
    if not value["enabled"] or time.time() >= value["expires"]:
        raise ValueError("inactive_field_approval")
    if value["used"] >= value["max_sessions"]:
        raise ValueError("exhausted_field_approval")
    if not SHA.fullmatch(value["source_revision"]):
        raise ValueError("invalid_source_revision")
    return value


def frozen_matches(pin):
    frozen = module_at(FREEZE)
    if frozen.source_revision() != pin["source_revision"]:
        raise ValueError("frozen_revision_mismatch")
    if frozen.source_hashes() != (pin["launcher_sha256"],
                                  pin["remote_bundle_sha256"]):
        raise ValueError("frozen_collector_mismatch")
    loaded = frozen.load_policy()
    if loaded != pin:
        raise ValueError("frozen_policy_mismatch")
    return True


def unit_text():
    strict_regular(SERVICE, size=8192)
    strict_regular(TIMER, size=8192)
    with SERVICE.open("r", encoding="utf-8") as handle:
        content = handle.read(8193)
    if (content.count(OLD_EXEC) != 1
            or NEW_EXEC in content or content.count("ExecStart=") != 1
            or content.count(OLD_WORKDIR) != 1
            or NEW_WORKDIR in content
            or content.count("WorkingDirectory=") != 1):
        raise ValueError("unrecognized_field_service")
    timer = TIMER.read_text(encoding="utf-8")
    if ("Unit=redux-field-bridge.service" not in timer
            or "OnCalendar=*-*-* *:20:00" not in timer):
        raise ValueError("unrecognized_field_timer")
    return content, content.replace(OLD_EXEC, NEW_EXEC).replace(
        OLD_WORKDIR, NEW_WORKDIR)


def private_folder(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("untrusted_frozen_directory")


def rewrite_service(payload):
    """Atomic service replacement with owner-only temporary file."""
    strict_regular(SERVICE, size=8192)
    fd, filename = tempfile.mkstemp(prefix="redux-field.", dir=SERVICE.parent)
    try:
        os.fchmod(fd, stat.S_IMODE(SERVICE.stat().st_mode))
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, SERVICE)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def migration():
    if ROOT != (Path.home() / "redux-workbench-src").resolve():
        raise ValueError("unexpected_source_checkout")
    pin = approval()
    existing, replacement = unit_text()
    if FREEZE.exists() or FREEZE.is_symlink():
        raise ValueError("frozen_worktree_already_exists")
    if command(["git", "cat-file", "-t", pin["source_revision"]],
               cwd=ROOT).stdout.strip() != "commit":
        raise ValueError("approved_source_revision_missing")
    private_folder(FREEZE.parent)
    service_state = command(["systemctl", "--user", "is-active", "--quiet",
                             "redux-field-bridge.service"])
    if service_state.returncode == 0:
        raise ValueError("field_observer_currently_running")
    # Acquiring the SAME lock as the scheduled collector prevents a race
    # while a run starts or the service is changed.
    lockfd = os.open(STATE / "collector.lock", os.O_RDWR | os.O_CREAT |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(lockfd, "a+b") as lock:
        meta = os.fstat(lock.fileno())
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid()
                or stat.S_IMODE(meta.st_mode) != 0o600):
            raise ValueError("untrusted_field_lock")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("field_observer_busy") from exc
        if command(["systemctl", "--user", "is-active", "--quiet",
                    "redux-field-bridge.service"]).returncode == 0:
            raise ValueError("field_observer_currently_running")
        try:
            out = command(["git", "worktree", "add", "--detach", str(FREEZE),
                           pin["source_revision"]], cwd=ROOT, deadline=90)
            if out.returncode:
                raise ValueError("frozen_worktree_creation_failed")
            frozen_matches(pin)
            # No timer or approval file changes. The next scheduled run
            # executes the same revision and consumes the same attempt ledger.
            rewrite_service(replacement)
            try:
                out = command(["systemctl", "--user", "daemon-reload"])
                if out.returncode:
                    raise ValueError("systemd_reload_failed")
            except (OSError, ValueError):
                # Even a missing/timed-out systemctl must restore the original
                # service file; do not leave a half-migrated active observer.
                rewrite_service(existing)
                try:
                    command(["systemctl", "--user", "daemon-reload"])
                except (OSError, ValueError):
                    pass
                raise ValueError("systemd_reload_failed")
        except Exception:
            # Leave a validated worktree available for offline diagnosis.
            # Original service is preserved until reload succeeds.
            raise
    return {
        "schema": 1,
        "result": "frozen",
        "observer_service_updated": True,
        "timer_schedule_changed": False,
        "read_only_approval_changed": False,
        "session_budget_reset": False,
        "hardware_action_performed": False,
        "codex_given_ssh_access": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true",
                       help="Describe migration with NO changes and NO Pi access")
    group.add_argument("--migrate", action="store_true",
                       help="Rebind approved local user service without Pi access")
    args = parser.parse_args(argv)
    if args.plan:
        print(json.dumps({
            "schema": 1, "mode": "freeze_existing_approval",
            "default": "offline_no_changes", "changes_pi": False,
            "approval_extended": False, "ssh_contact": False,
            "source_revision": "existing_approved_revision_only",
            "user_service_execstart_rebound": True,
            "timer_schedule_changed": False,
        }, sort_keys=True))
        return 0
    try:
        print(json.dumps(migration(), sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        # Human-visible error codes are fixed; never print key/path/device
        # material or subprocess stderr.
        error = str(exc)
        known = {"unexpected_source_checkout", "inactive_field_approval",
                 "exhausted_field_approval", "invalid_source_revision",
                 "frozen_revision_mismatch", "frozen_collector_mismatch",
                 "frozen_policy_mismatch", "unrecognized_field_service",
                 "unrecognized_field_timer", "frozen_worktree_already_exists",
                 "approved_source_revision_missing", "field_observer_currently_running",
                 "field_observer_busy", "untrusted_local_state",
                 "untrusted_local_file", "untrusted_local_permissions",
                 "oversized_local_file", "untrusted_frozen_directory",
                 "untrusted_field_lock", "local_command_unavailable",
                 "frozen_worktree_creation_failed", "systemd_reload_failed"}
        print(json.dumps({"schema": 1, "result": "blocked",
                          "reason": error if error in known else "migration_failed",
                          "hardware_action_performed": False}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
