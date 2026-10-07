#!/usr/bin/env python3
"""Lane guard — fail a PR that edits files outside its branch's lane.

This is the ENFORCEMENT behind ASSIGNMENTS.md: lanes stop being a polite request
and become a CI gate. An agent's PR may only touch the paths its branch prefix
owns. Shared/structural files (.github/**, the top-level docs, every package
__init__, pyproject, CI) are LEAD-ONLY — that is the rule that stops another
agent's branch from dragging a workflow or a shared-file edit into main.

Branch prefixes → lanes:
  claude/*, human/*   lead / owner: may touch anything
  codex/*             the image/OS build only
  grok/*              the detector module only
  <anything else>     rejected (no lane)

Runs in GitHub Actions on pull_request. Fails closed on an out-of-lane file;
fails *open* (exit 0 with a note) only if it genuinely cannot compute the diff,
so a tooling hiccup never blocks legitimate work.
"""
from __future__ import annotations

import os
import subprocess
import sys
import fnmatch


# --- per-lane allowed path globs (non-lead lanes) --------------------------- #
LANES = {
    "codex/": [
        "image/**", "pi-gen/**", "boot/**", "build.sh",
        "docs/IMAGE_BUILD.md",
        "redux/core/boot.py",                 # accepted service-entry seam
        "tests/test_image_build.py", "tests/test_nexmon_build.py", "tests/test_boot.py",
    ],
    "grok/": [
        "redux/detect/**",
        "tests/test_detect_*.py",
        "redux/geo/**",
        "tests/test_geo_*.py",
    ],
}
LEAD_PREFIXES = ("claude/", "human/")


def _run(*args) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def changed_files(base: str) -> list:
    # three-dot: changes on HEAD since it diverged from base
    for ref in (f"origin/{base}", base):
        out = _run("git", "diff", "--name-only", f"{ref}...HEAD")
        if out:
            return [f for f in out.splitlines() if f.strip()]
    # fall back to the last commit if no base diff resolved
    out = _run("git", "diff", "--name-only", "HEAD~1...HEAD")
    return [f for f in out.splitlines() if f.strip()]


def allowed(path: str, globs: list) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def main() -> int:
    head = os.environ.get("GITHUB_HEAD_REF", "").strip()
    base = os.environ.get("GITHUB_BASE_REF", "main").strip() or "main"

    if not head:
        print("lane-guard: no PR head ref (not a PR event) — skipping.")
        return 0

    # lead / owner lanes may touch anything
    if head.startswith(LEAD_PREFIXES):
        print(f"lane-guard: '{head}' is a lead/owner lane — full access. OK.")
        return 0

    lane = next((p for p in LANES if head.startswith(p)), None)
    if lane is None:
        print(f"lane-guard: branch '{head}' has no assigned lane.\n"
              f"  Use a known prefix: claude/ human/ codex/ grok/ (see ASSIGNMENTS.md).")
        return 1

    files = changed_files(base)
    if not files:
        print("lane-guard: could not determine changed files — passing (fail-open).")
        return 0

    globs = LANES[lane]
    violations = [f for f in files if not allowed(f, globs)]
    if violations:
        print(f"lane-guard: branch '{head}' (lane '{lane}') edited files outside its lane:")
        for f in violations:
            print(f"    ✗ {f}")
        print("\n  This lane may only touch:")
        for g in globs:
            print(f"    - {g}")
        print("\n  Shared/structural files (.github/**, README/AGENTS/TASKS/ASSIGNMENTS,\n"
              "  docs/*, every __init__.py, pyproject, CI) are LEAD-ONLY. Move cross-lane\n"
              "  changes to the lead (claude/*) and keep your PR inside your lane.")
        return 1

    print(f"lane-guard: branch '{head}' stayed inside lane '{lane}' "
          f"({len(files)} file(s) checked). OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
