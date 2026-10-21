#!/usr/bin/env python3
"""Optional unattended source-only Codex cycle in an ephemeral checkout.

There is intentionally NO SSH, Pi integration, sudo, deployment, flash,
network probing, RF operation, secret upload, or force-push path in this file.
A cycle requires --enable-agent (the local timer uses the same explicit flag).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO = "https://github.com/patrickato/pwnagotchi-redux.git"
BRANCH = "human/redux-workbench"
# Approval policy is a root option on both newer and older Codex CLI
# releases; placing it after 'exec' fails in some shipped versions.
CODEX_COMMAND = ("codex", "--sandbox", "workspace-write",
                 "--ask-for-approval", "never", "exec", "-")
ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / ".local" / "state" / "redux-autopilot"
WORK = Path.home() / ".local" / "share" / "redux-autopilot"
EDITABLE = ("redux/", "tests/", "scripts/", "docs/", "image/", "boot/")
PROTECTED = {
    "scripts/redux-autopilot.py",
    "scripts/install-redux-autopilot.sh",
    "docs/AUTOPILOT.md",
    "docs/AUTOPILOT_AGENT.md",
}
MAX_CHANGED = 35
MAX_SINGLE_BYTES = 512 * 1024


def invoke(argv, *, cwd=None, timeout=120, stdin=None, env=None):
    """Execute with no inherited terminal or interactive prompts."""
    try:
        result = subprocess.run(
            argv, cwd=cwd, input=stdin, text=True, capture_output=True,
            timeout=timeout, check=False, env=env,
        )
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 124, ""


def git(cwd, *args, timeout=120):
    return invoke(["git", *args], cwd=cwd, timeout=timeout)


def changed_names(cwd):
    """Parse NUL-delimited porcelain; disallow renames/copies and deletions."""
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=30, check=False,
    )
    if result.returncode:
        raise ValueError("worktree_status_unavailable")
    entries = result.stdout.split(b"\0")
    paths = []
    for entry in entries:
        if not entry:
            continue
        if len(entry) < 4 or entry[2:3] != b" ":
            raise ValueError("invalid_git_status")
        state = entry[:2].decode("ascii", "replace")
        if any(kind in state for kind in "RCDU") or state == "!!":
            raise ValueError("rename_copy_delete_or_conflict")
        try:
            name = entry[3:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid_filename") from exc
        paths.append(name)
    return paths


def allowable(cwd, paths):
    """Defense in depth; not a substitute for reviewing generated code."""
    if len(paths) > MAX_CHANGED:
        return False, "too_many_changes"
    if not paths:
        return True, "no_changes"
    for name in paths:
        if (
            name in PROTECTED or name.startswith(".") or
            not name.startswith(EDITABLE) or
            ".." in Path(name).parts or
            not re.fullmatch(r"[A-Za-z0-9_./+-]{1,200}", name)
        ):
            return False, "restricted_path"
        source = cwd / name
        # Reject symlinked parents as well as the final component; a path
        # inside an allowed tree must never traverse out through an alias.
        relative = Path(name)
        parents = list(relative.parents)[:-1]
        if any((cwd / parent).is_symlink() for parent in parents):
            return False, "symlinked_parent"
        if not source.is_file() or source.is_symlink():
            return False, "nonregular_or_missing_output"
        if source.stat().st_size > MAX_SINGLE_BYTES:
            return False, "oversized_output"
    return True, "bounded_sources"


def record(status, *, reason, revision=None, changed=None):
    """Only bounded non-sensitive outcome codes persist on the workstation."""
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = STATE / "last.json"
    data = {
        "schema": 1, "status": status,
        "reason": reason if re.fullmatch(r"[a-z0-9_]{1,60}", reason) else "invalid_reason",
        "revision": revision if isinstance(revision, str) and re.fullmatch(r"[a-f0-9]{40}", revision) else None,
        "changed_count": changed if type(changed) is int and 0 <= changed <= MAX_CHANGED else None,
        "observed_at": int(time.time()),
        "scope": "offline_source_only",
    }
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, sort_keys=True)
        stream.write("\n")
    print(json.dumps(data, sort_keys=True))


def cycle():
    if shutil.which("codex") is None:
        record("blocked", reason="codex_cli_unavailable")
        return 2
    if shutil.which("git") is None or shutil.which("python3") is None:
        record("blocked", reason="host_tool_unavailable")
        return 2
    prompt_file = ROOT / "docs" / "AUTOPILOT_AGENT.md"
    if not prompt_file.is_file():
        record("blocked", reason="prompt_not_found")
        return 2

    WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Only one cycle can use the owner's credentials and push at a time.
    import fcntl
    with (WORK / "lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            record("blocked", reason="another_cycle_active")
            return 2

        with tempfile.TemporaryDirectory(prefix="cycle-", dir=WORK) as folder:
            checkout = Path(folder) / "worktree"
            environment = dict(os.environ)
            environment.pop("SSH_AUTH_SOCK", None)
            environment["GIT_TERMINAL_PROMPT"] = "0"
            status, _ = invoke(
                ["git", "clone", "--quiet", "--depth", "1", "--single-branch",
                 "--branch", BRANCH, REPO, str(checkout)],
                timeout=180, env=environment,
            )
            if status:
                record("blocked", reason="clone_failed")
                return 2
            code, revision = git(checkout, "rev-parse", "HEAD")
            revision = revision.strip()
            if code or not re.fullmatch(r"[a-f0-9]{40}", revision):
                record("blocked", reason="revision_unknown")
                return 2
            # A clone does not inherit the source checkout's repo-local
            # author identity. Reuse only validated author metadata, never
            # copy .git/config or credentials from the operator checkout.
            author = {}
            for key in ("user.name", "user.email"):
                code, value = git(ROOT, "config", "--get", key)
                value = value.strip()
                if (code or not value or len(value) > 160 or
                        any(c in value for c in "\r\n\x00")):
                    record("blocked", reason="git_author_unavailable", revision=revision)
                    return 2
                author[key] = value
            for key, value in author.items():
                code, _ = git(checkout, "config", "--local", key, value)
                if code:
                    record("blocked", reason="git_author_unavailable", revision=revision)
                    return 2

            instruction = prompt_file.read_text(encoding="utf-8")
            codex_env = dict(environment)
            # Source-only Codex should not be given GitHub/SSH deployment
            # credentials as ambient environment variables.
            for key in list(codex_env):
                if key.startswith(("AWS_", "GITHUB_", "GH_", "PI_", "SSH_")):
                    codex_env.pop(key, None)
            try:
                codex = subprocess.run(
                    list(CODEX_COMMAND),
                    cwd=checkout, input=instruction, text=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=2700, check=False, env=codex_env,
                )
                exit_code = codex.returncode
            except (OSError, subprocess.TimeoutExpired):
                exit_code = 124
            if exit_code:
                record("blocked", reason="codex_execution_failed", revision=revision)
                return 2
            code, new_head = git(checkout, "rev-parse", "HEAD")
            if code or new_head.strip() != revision:
                record("blocked", reason="agent_changed_git_history", revision=revision)
                return 2
            try:
                paths = changed_names(checkout)
                valid, why = allowable(checkout, paths)
            except (ValueError, OSError, subprocess.TimeoutExpired):
                valid, why, paths = False, "unsafe_git_status", []
            if not valid:
                record("blocked", reason=why, revision=revision)
                return 2
            if not paths:
                record("idle", reason="no_source_change", revision=revision, changed=0)
                return 0

            # The wrapper, not Codex, owns final tests and Git publication.
            for argv, timeout, reason in (
                (["python3", "-m", "pytest", "-q"], 1500, "python_tests_failed"),
                (["bash", "image/ci.sh"], 1500, "image_tests_failed"),
                (["python3", "-m", "compileall", "-q", "redux", "scripts", "boot", "image"],
                 120, "compile_failed"),
                (["git", "diff", "--check"], 60, "whitespace_failed"),
            ):
                code, _ = invoke(argv, cwd=checkout, timeout=timeout, env=environment)
                if code:
                    record("blocked", reason=reason, revision=revision, changed=len(paths))
                    return 2

            code, _ = git(checkout, "add", "-A", "--", *EDITABLE)
            if code:
                record("blocked", reason="git_stage_failed", revision=revision)
                return 2
            code, _ = git(checkout, "diff", "--cached", "--check")
            if code:
                record("blocked", reason="staged_diff_invalid", revision=revision)
                return 2
            code, _ = git(checkout, "commit", "-m", "Autopilot: tested source-only Codex cycle")
            if code:
                record("blocked", reason="git_commit_failed", revision=revision)
                return 2
            # Fast-forward only; a competing agent's push causes a safe stop.
            code, _ = git(checkout, "push", "origin", "HEAD:refs/heads/" + BRANCH, timeout=120)
            if code:
                record("blocked", reason="git_push_rejected", revision=revision,
                       changed=len(paths))
                return 2
            code, published = git(checkout, "rev-parse", "HEAD")
            record("published", reason="tests_passed_fast_forward_push",
                   revision=published.strip() if code == 0 else None, changed=len(paths))
            return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "cycle"))
    parser.add_argument("--enable-agent", action="store_true",
                        help="Explicitly authorize one source-only Codex cycle")
    args = parser.parse_args(argv)
    if args.mode == "plan":
        print(json.dumps({
            "source_branch": BRANCH, "pi_access": False, "sudo": False,
            "radio_changes": False, "card_changes": False,
            "publishing": "non_force_push_only_after_python_and_image_tests",
            "source_checkout": "ephemeral", "approval": "required_for_installation",
            "runs_when": "invoked_by_operator_or_explicitly_enabled_user_timer",
        }, sort_keys=True))
        return 0
    if not args.enable_agent:
        parser.error("cycle requires --enable-agent; no task was started")
    return cycle()


if __name__ == "__main__":
    raise SystemExit(main())
