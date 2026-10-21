#!/usr/bin/env python3
"""Opt-in, rate-limited physical Pi observation bridge.

This process is NOT Codex, cannot accept arbitrary remote commands and does
not ship a private key or raw field report to GitHub. Operator-approved local
policy explicitly authorizes recurring reads through the already reviewed
memory-only redux-acceptance.py SSH collector. Default mode is OFFLINE PLAN.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / ".local" / "state" / "redux-field-bridge"
SCHEMA = 1
MIN_INTERVAL = 4 * 3600
MAX_DURATION_DAYS = 14
MAX_SESSIONS = 84
ALLOWED_STATES = ("observed", "unknown", "expired", "rate_limited",
                  "exhausted", "unavailable", "revoked", "not_enrolled",
                  "source_changed")
POLICY_FILE = "recurring-approval.json"


def plan():
    return {
        "schema": SCHEMA, "mode": "read_only_session",
        "default_network_action": False,
        "enrollment_requires_explicit_operator_approval": True,
        "runs_require_unexpired_private_local_policy": True,
        "minimum_interval_hours": MIN_INTERVAL // 3600,
        "maximum_approval_days": MAX_DURATION_DAYS,
        "maximum_sessions": MAX_SESSIONS,
        "ssh_public_key_only": True,
        "strict_known_hosts": True,
        "remote_collector_timeout_seconds": 45,
        "no_sudo": True, "no_pi_writes": True,
        "no_packet_sockets": True, "no_rf_changes": True,
        "no_service_changes": True, "no_pcap_reads": True,
        "no_deployment": True, "no_reboot": True,
        "publish_private_evidence_to_github": False,
        "physical_acceptance": "UNKNOWN",
    }


def source_hashes():
    """Pin the reviewed launcher and exact remote in-memory module bundle."""
    script = ROOT / "scripts" / "redux-acceptance.py"
    if script.is_symlink() or not script.is_file() or script.stat().st_size > 128 * 1024:
        raise ValueError("unsafe_collector_source")
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    spec = importlib.util.spec_from_file_location("bridge_acceptance", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import hashlib as _hashlib
    _pi, _field, remote_bundle = module.session_support()
    return digest, _hashlib.sha256(remote_bundle.encode()).hexdigest()


def private_state():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = STATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("untrusted_local_state")
    if info.st_uid != os.getuid():
        raise ValueError("unowned_local_state")
    return STATE


def private_file(path: Path):
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_uid != os.getuid()):
        raise ValueError("untrusted_local_policy")


def write_json(name, value, *, overwrite=False):
    root = private_state()
    target = root / name
    raw = (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode()
    if len(raw) > 8192:
        raise ValueError("oversized_local_record")
    if not re.fullmatch(r"[a-z0-9_.-]{1,80}", name):
        raise ValueError("bad_local_filename")
    if not overwrite and (target.exists() or target.is_symlink()):
        raise ValueError("existing_local_record")
    temp = root / (name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temp.exists():
            temp.unlink()


def private_ip(value):
    if type(value) is not str:
        raise ValueError("invalid_ip_type")
    ip = ipaddress.ip_address(value)
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        raise ValueError("unsafe_ip_target")
    return str(ip)


def key_path(value):
    if type(value) is not str and not isinstance(value, Path):
        raise ValueError("invalid_key_path_type")
    key = Path(value).expanduser().absolute()
    if not key.is_file() or key.is_symlink():
        raise ValueError("key_unavailable")
    meta = key.stat()
    if (meta.st_uid != os.getuid() or
            stat.S_IMODE(meta.st_mode) & 0o077):
        raise ValueError("key_permission_untrusted")
    return str(key)


def validate_policy(value):
    if not isinstance(value, dict) or set(value) != {
        "schema", "host", "key_path", "created", "expires",
        "interval_seconds", "max_sessions", "used", "last_attempt", "enabled",
        "launcher_sha256", "remote_bundle_sha256"
    } or value["schema"] != SCHEMA:
        raise ValueError("invalid_approval_schema")
    private_ip(value["host"])
    key_path(value["key_path"])
    if (not isinstance(value["launcher_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["launcher_sha256"])
            or not isinstance(value["remote_bundle_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["remote_bundle_sha256"])):
        raise ValueError("invalid_source_pins")
    if (type(value["created"]) is not int or type(value["expires"]) is not int
            or not 0 < value["created"] < value["expires"]
            or value["expires"] - value["created"] > MAX_DURATION_DAYS * 86400
            or type(value["interval_seconds"]) is not int
            or value["interval_seconds"] < MIN_INTERVAL
            or type(value["max_sessions"]) is not int
            or not 1 <= value["max_sessions"] <= MAX_SESSIONS
            or type(value["used"]) is not int
            or not 0 <= value["used"] <= value["max_sessions"]
            or value["last_attempt"] is not None
            and (type(value["last_attempt"]) is not int or value["last_attempt"] < value["created"])
            or type(value["enabled"]) is not bool):
        raise ValueError("invalid_approval_bounds")
    return value


def load_policy():
    root = private_state()
    target = root / POLICY_FILE
    private_file(target)
    with target.open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise ValueError("oversized_local_policy")
    def reject_duplicate_fields(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate_policy_field")
            value[key] = item
        return value
    return validate_policy(json.loads(raw, object_pairs_hook=reject_duplicate_fields))


def enroll(host, key, *, interval_hours, days, sessions, approve, now=None):
    if not approve:
        raise ValueError("explicit_recurring_approval_required")
    if (type(interval_hours) is not int or interval_hours < 4
            or type(days) is not int or not 1 <= days <= MAX_DURATION_DAYS
            or type(sessions) is not int or not 1 <= sessions <= MAX_SESSIONS):
        raise ValueError("unsafe_recurring_scope")
    now = int(time.time()) if now is None else int(now)
    launcher_digest, remote_digest = source_hashes()
    policy = {
        "schema": SCHEMA, "host": private_ip(host),
        "key_path": key_path(key),
        "created": now, "expires": now + days * 86400,
        "interval_seconds": interval_hours * 3600,
        "launcher_sha256": launcher_digest,
        "remote_bundle_sha256": remote_digest,
        "max_sessions": sessions, "used": 0,
        "last_attempt": None, "enabled": True,
    }
    validate_policy(policy)
    write_json(POLICY_FILE, policy)
    return {"schema": SCHEMA, "status": "enrolled", "expires_in_days": days,
            "max_sessions": sessions, "interval_hours": interval_hours,
            "hardware_action_performed": False}


def invoke_collector(policy, evidence, *, runner=subprocess.run):
    command = [
        sys.executable, str(ROOT / "scripts" / "redux-acceptance.py"),
        "--json", "session-collect",
        "--host", policy["host"],
        "--key", policy["key_path"],
        "--card-role", "unverified",
        "--output", str(evidence),
        "--authorize-read-only",
    ]
    try:
        result = runner(command, cwd=ROOT, capture_output=True, text=True,
                        timeout=75, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if len(result.stdout) > 131072:
        return False
    try:
        output = json.loads(result.stdout)
    except (TypeError, ValueError):
        return False
    # Collector exits 3 for UNKNOWN physical acceptance even when an unsigned
    # read-only report was saved. Never promote it into physical PASS.
    return (result.returncode in (0, 3) and
            output.get("session_report_saved") is True and
            output.get("physical_acceptance") == "UNKNOWN" and
            output.get("network_actions_performed") is True and
            evidence.is_file() and not evidence.is_symlink() and
            stat.S_IMODE(evidence.stat().st_mode) == 0o600)


def run_once(*, now=None, runner=subprocess.run):
    root = private_state()
    lock_fd = os.open(root / "collector.lock",
                      os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                      0o600)
    with os.fdopen(lock_fd, "a+b") as lock:
        fd_info = os.fstat(lock.fileno())
        if (not stat.S_ISREG(fd_info.st_mode)
                or stat.S_IMODE(fd_info.st_mode) != 0o600
                or fd_info.st_uid != os.getuid()):
            raise ValueError("untrusted_local_lock")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"schema": SCHEMA, "status": "rate_limited", "performed": False}
        try:
            policy = load_policy()
        except FileNotFoundError:
            return {"schema": SCHEMA, "status": "not_enrolled", "performed": False}
        clock = int(time.time()) if now is None else int(now)
        if not policy["enabled"]:
            status = "revoked"
        elif clock >= policy["expires"] or clock < policy["created"]:
            status = "expired"
        elif source_hashes() != (
            policy["launcher_sha256"], policy["remote_bundle_sha256"]
        ):
            status = "source_changed"
        elif policy["used"] >= policy["max_sessions"]:
            status = "exhausted"
        elif (policy["last_attempt"] is not None and
              clock - policy["last_attempt"] < policy["interval_seconds"]):
            status = "rate_limited"
        else:
            status = None
        if status is not None:
            return {"schema": SCHEMA, "status": status, "performed": False}

        # Consume this attempt *before* initiating SSH. Interrupted/crashed
        # attempts cannot automatically retry the Pi.
        policy["used"] += 1
        policy["last_attempt"] = clock
        write_json(POLICY_FILE, policy, overwrite=True)
        private = root / ("session-" + uuid.uuid4().hex + ".json")
        successful = invoke_collector(policy, private, runner=runner)
        result = {
            "schema": SCHEMA, "status": "observed" if successful else "unavailable",
            "performed": True, "physical_acceptance": "UNKNOWN",
            "sessions_used": policy["used"],
            "sessions_remaining": policy["max_sessions"] - policy["used"],
            "report_saved_private": successful,
            "no_pi_changes_requested": True,
        }
        write_json("last-status.json", result, overwrite=True)
        return result


def revoke():
    try:
        policy = load_policy()
    except FileNotFoundError:
        return {"schema": SCHEMA, "status": "not_enrolled", "performed": False}
    policy["enabled"] = False
    write_json(POLICY_FILE, policy, overwrite=True)
    return {"schema": SCHEMA, "status": "revoked", "performed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("plan", help="No filesystem or Pi operations")
    setup = sub.add_parser("enroll", help="One explicit time-limited recurring read-only approval")
    setup.add_argument("--host", required=True)
    setup.add_argument("--key", required=True)
    setup.add_argument("--interval-hours", type=int, default=6)
    setup.add_argument("--days", type=int, default=14)
    setup.add_argument("--max-sessions", type=int, default=40)
    setup.add_argument("--approve-recurring-read-only", action="store_true")
    sub.add_parser("observe", help="Run once only under existing local approval")
    sub.add_parser("revoke", help="Disable future observations without Pi contact")
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            print(json.dumps(plan(), sort_keys=True))
            return 0
        if args.mode == "enroll":
            output = enroll(args.host, args.key, interval_hours=args.interval_hours,
                            days=args.days, sessions=args.max_sessions,
                            approve=args.approve_recurring_read_only)
        elif args.mode == "revoke":
            output = revoke()
        else:
            output = run_once()
        print(json.dumps(output, sort_keys=True))
        return 0 if output.get("status") in ("enrolled", "observed", "revoked") else 3
    except (ValueError, OSError, json.JSONDecodeError):
        # Never print a private path, key, IP, unfiltered device result or trace.
        print(json.dumps({"schema": SCHEMA, "status": "unavailable", "performed": False}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
