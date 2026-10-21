#!/usr/bin/env python3
"""Single-approval, NO-SUDO Pi prepared-artifact SHA audit via strict SSH.

This launcher never exports raw captures or hc22000 data. It sends the
repository's existing audited Python source to RAM on the approved Pi, checks
the dedicated REDUXCAP mount, audits at most ONE ready output, and returns
bounded aggregate status. The one-time local attempt is consumed BEFORE SSH.
Default --plan performs no disk or network I/O.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / ".local/state/redux-one-artifact-audit"
AUDITOR = ROOT / "scripts/capture-artifact-audit.py"
MAX_SOURCE_BYTES = 64 * 1024
MAX_REMOTE_BYTES = 8192
FORMAT = "redux-one-prepared-artifact-audit-v1"
KNOWN = frozenset(("verified_sha256_inode", "mount_unverified", "mount_changed",
                   "ledger_unavailable", "ledger_changed", "no_ready_row",
                   "row_scope_exceeded", "artifact_integrity_problem",
                   "audit_unavailable", "execution_unavailable"))
BOOTSTRAP = ("import sys; d=sys.stdin.buffer.read(65537); "
             "assert 0<len(d)<=65536, 'oversized source'; "
             "exec(compile(d,'<bounded-one-artifact-audit>','exec'),"
             "{'__name__':'__main__'})")

# This static launcher is executed *in memory* by the existing pi SSH user.
# It never uses sudo, changes a wireless interface, writes a receipt or
# decodes or transfers capture contents. The tested audit() implementation
# from capture-artifact-audit.py is inserted into the same in-memory program.
REMOTE_PRELUDE = r"""
import json
import os
from pathlib import Path
import stat
import sys
from urllib.parse import unquote

ROOT = Path("/captures")
LABEL = Path("/dev/disk/by-label/REDUXCAP")
LEDGER = ROOT / "jobs.db"
PREPARED = ROOT / "ready"

def result(status, reason, checked=0, ready_rows=0):
    return {"schema":1,"format":"redux-one-prepared-artifact-audit-v1",
            "status":status,"reason":reason,"checked":checked,
            "ready_rows":ready_rows,"physical_acceptance":"UNKNOWN",
            "handshake_authenticity":"UNKNOWN","network_changes":False,
            "pi_file_writes_requested":False}

def mount_identity():
    # A real ext4 rw /captures partition identified by its REDUXCAP
    # block-device label; never a rootfs fallback or symlinked mount name.
    try:
        root_stat = ROOT.lstat()
        label_stat = LABEL.stat()
        if (not stat.S_ISDIR(root_stat.st_mode)
                or not stat.S_ISBLK(label_stat.st_mode)
                or root_stat.st_dev != label_stat.st_rdev):
            return None
        matches = []
        with open("/proc/self/mountinfo", encoding="utf8") as stream:
            for line in stream:
                pair = line.strip().split(" - ", 1)
                if len(pair) != 2:
                    continue
                pre = pair[0].split()
                post = pair[1].split()
                if len(pre) >= 6 and len(post) >= 3 and pre[4] == "/captures":
                    matches.append((pre, post))
        if len(matches) != 1:
            return None
        pre, post = matches[0]
        major_minor = f"{os.major(root_stat.st_dev)}:{os.minor(root_stat.st_dev)}"
        if (pre[2] != major_minor or pre[3] != "/"
                or post[0] != "ext4" or "rw" not in pre[5].split(",")):
            return None
        return (root_stat.st_dev, root_stat.st_ino, pre[2])
    except (OSError, ValueError, OverflowError):
        return None

def run_once():
    if os.geteuid() == 0:
        return result("unknown", "audit_unavailable")
    first_mount = mount_identity()
    if first_mount is None:
        return result("unknown", "mount_unverified")
    if (LEDGER.is_symlink() or PREPARED.is_symlink()
            or not LEDGER.is_file() or not PREPARED.is_dir()):
        return result("unknown", "ledger_unavailable")
    # Module source is sent by the owner-approved WSL launcher, in memory.
    # __name__ is not '__main__': the general-purpose audit CLI does not run.
    library = {"__name__":"reviewed_capture_artifact_audit",
               "__file__":"<bundled-capture-audit>"}
    exec(compile(AUDITOR_SOURCE, "<reviewed-capture-audit>", "exec"), library)
    audited = library["audit"](LEDGER, PREPARED, max_rows=1)
    after_mount = mount_identity()
    if after_mount != first_mount:
        return result("unknown", "mount_changed")
    checked = audited.get("checked")
    ready = audited.get("ready_rows")
    if (type(checked) is not int or type(ready) is not int
            or checked < 0 or checked > 1 or ready < 0 or ready > 100000):
        return result("unknown", "audit_unavailable")
    issues = audited.get("issues")
    if not isinstance(issues, dict):
        return result("unknown", "audit_unavailable")
    if ready > 1 or "row_limit_exceeded" in issues:
        return result("unknown", "row_scope_exceeded", 0, min(ready, 100000))
    if ready == 0 and audited.get("result") == "unknown":
        return result("unknown", "no_ready_row")
    if audited.get("result") == "pass" and checked == ready == 1 and not issues:
        return result("verified_bytes", "verified_sha256_inode", 1, 1)
    if "ledger_changed" in issues:
        return result("unknown", "ledger_changed", checked, ready)
    if "ledger_unavailable" in issues:
        return result("unknown", "ledger_unavailable", checked, ready)
    if audited.get("result") == "attention":
        return result("attention", "artifact_integrity_problem", checked, ready)
    return result("unknown", "audit_unavailable", checked, ready)
"""

REMOTE_EPILOGUE = r"""
try:
    final = run_once()
except (OSError, ValueError, KeyError, TypeError, OverflowError, ImportError):
    final = result("unknown", "audit_unavailable")
print(json.dumps(final, sort_keys=True, allow_nan=False))
"""


def plan():
    return {"schema":1,"format":FORMAT,"mode":"one_shot_read_only",
            "target":"existing_private_field_approval_host",
            "ready_row_max":1, "prepared_bytes_max":33*1024*1024,
            "hash_total_max":512*1024*1024,
            "strict_known_hosts":True, "sudo":False, "single_ssh_attempt":True,
            "max_remote_seconds":70,"one_local_attempt_token":True,
            "verifies_ext4_reduxcap_mount":True,
            "remote_writes_requested":False, "raw_capture_export":False,
            "prepared_artifact_export":False, "no_rf_activity":True,
            "publication_to_github":False,
            "physical_acceptance":"UNKNOWN",
            "handshake_authenticity":"UNKNOWN"}


def private_parent():
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    metadata=STATE.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid!=os.getuid()
            or stat.S_IMODE(metadata.st_mode)!=0o700):
        raise ValueError("unsafe_local_state")
    return STATE


def single_attempt_token():
    base=private_parent()
    fd=os.open(base/"consumed.json",
               os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd,"w",encoding="utf8") as stream:
            stream.write('{"schema":1,"approval":"single_readonly_artifact_audit","consumed":true}\n')
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        pass


def approved_host_key():
    # Reuse the signed-off strict-SSH target/key *identity* without reusing
    # the metadata observer's separate hourly session attempt budget.
    src=ROOT/"scripts/redux-field-bridge.py"
    spec=importlib.util.spec_from_file_location("approved_field_identity",src)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value=module.load_policy()
    if (not value["enabled"] or time.time()>=value["expires"]
            or value["used"]>=value["max_sessions"]):
        raise ValueError("field_identity_scope_inactive")
    # Field bridge loads a strictly private 0600 policy and validates IP
    # and owner-only key mode/identity. Never emit either into status output.
    return module.private_ip(value["host"]),module.key_path(value["key_path"])


def reviewed_payload():
    # One identical, clean and tested Git workbench checkout; never silently
    # execute a dirty, untracked or locally patched auditor on the Pi.
    def git(*args):
        response=subprocess.run(["git",*args],cwd=ROOT,
                                capture_output=True,text=True,timeout=8,check=False)
        if response.returncode:
            raise ValueError("untrusted_auditor_source")
        return response.stdout.strip()
    if git("branch","--show-current")!="human/redux-workbench":
        raise ValueError("untrusted_auditor_branch")
    if git("status","--porcelain","--untracked-files=no"):
        raise ValueError("untrusted_dirty_checkout")
    source=AUDITOR.read_bytes()
    if not 0<len(source)<=MAX_SOURCE_BYTES:
        raise ValueError("untrusted_auditor_size")
    committed=subprocess.run(["git","show","HEAD:scripts/capture-artifact-audit.py"],
                             cwd=ROOT,capture_output=True,timeout=8,check=False)
    if committed.returncode!=0 or committed.stdout!=source:
        raise ValueError("uncommitted_auditor_source")
    # Both the remote scope and local identity are fixed; the only mutable
    # source sent to the Pi is the reviewed artifact-audit implementation.
    payload=(REMOTE_PRELUDE+"\nAUDITOR_SOURCE="+repr(source.decode("utf8"))+
             "\n"+REMOTE_EPILOGUE).encode("utf8")
    if len(payload)>MAX_SOURCE_BYTES:
        raise ValueError("oversized_remote_payload")
    return payload


def classify(raw, exit_code):
    if type(raw) is not str or len(raw.encode("utf8"))>MAX_REMOTE_BYTES:
        raise ValueError("invalid_remote_response")
    def unique(pairs):
        answer={}
        for key,value in pairs:
            if key in answer:
                raise ValueError("duplicate_response_field")
            answer[key]=value
        return answer
    response=json.loads(raw,object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    required={"schema","format","status","reason","checked","ready_rows",
              "physical_acceptance","handshake_authenticity","network_changes",
              "pi_file_writes_requested"}
    if (type(response) is not dict or set(response)!=required
            or response["schema"]!=1 or response["format"]!=FORMAT
            or response["status"] not in ("verified_bytes","attention","unknown")
            or response["reason"] not in KNOWN
            or response["physical_acceptance"]!="UNKNOWN"
            or response["handshake_authenticity"]!="UNKNOWN"
            or response["network_changes"] is not False
            or response["pi_file_writes_requested"] is not False
            or type(response["checked"]) is not int
            or type(response["ready_rows"]) is not int
            or not 0<=response["checked"]<=1
            or not 0<=response["ready_rows"]<=100000
            or (response["status"]=="verified_bytes"
                and (response["reason"]!="verified_sha256_inode"
                     or response["checked"]!=1 or response["ready_rows"]!=1))
            or (response["reason"]=="verified_sha256_inode"
                and response["status"]!="verified_bytes")
            or exit_code!=0):
        raise ValueError("invalid_remote_result_shape")
    return response


def one_shot(*, run=subprocess.run):
    payload=reviewed_payload()
    host,key=approved_host_key()
    # Authorization is consumed before SSH. A timeout, host mismatch, or
    # permissions failure does not automatically retry or widen the scope.
    single_attempt_token()
    ssh=[
        "ssh", "-T", "-o","BatchMode=yes",
        "-o","StrictHostKeyChecking=yes", "-o","ConnectTimeout=7",
        "-o","ConnectionAttempts=1", "-o","PasswordAuthentication=no",
        "-o","KbdInteractiveAuthentication=no", "-o","ForwardAgent=no",
        "-o","ClearAllForwardings=yes", "-o","RequestTTY=no",
        "-o","LogLevel=ERROR", "-i", key,
        "pi@"+host, "python3 -I -c "+shlex.quote(BOOTSTRAP),
    ]
    try:
        response=run(ssh,input=payload,capture_output=True,check=False,timeout=70)
    except (OSError,subprocess.TimeoutExpired):
        return unavailable()
    if response.returncode!=0 or type(response.stdout) is not bytes:
        return unavailable()
    try:
        return classify(response.stdout.decode("utf8"),response.returncode)
    except (UnicodeError,ValueError,TypeError):
        return unavailable()


def unavailable():
    return {"schema":1,"format":FORMAT,"status":"unknown",
            "reason":"execution_unavailable","checked":0,"ready_rows":0,
            "physical_acceptance":"UNKNOWN","handshake_authenticity":"UNKNOWN",
            "network_changes":False,"pi_file_writes_requested":False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    choices=parser.add_mutually_exclusive_group(required=True)
    choices.add_argument("--plan",action="store_true",help="No IO")
    choices.add_argument("--audit-once",action="store_true")
    parser.add_argument("--approve-one-ready-artifact-read-only",
                        action="store_true",help="Explicit separately granted one-shot owner approval")
    args=parser.parse_args(argv)
    if args.plan:
        print(json.dumps(plan(),sort_keys=True))
        return 0
    if not args.approve_one_ready_artifact_read_only:
        print(json.dumps(unavailable(),sort_keys=True))
        return 3
    try:
        outcome=one_shot()
    except (OSError,ValueError,subprocess.TimeoutExpired,UnicodeError):
        outcome=unavailable()
    print(json.dumps(outcome,sort_keys=True))
    return 0 if outcome["status"]=="verified_bytes" else 3


if __name__=="__main__":
    raise SystemExit(main())
