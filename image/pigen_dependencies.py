"""Adapt the pinned pi-gen dependency check to a verified ARM64 QEMU host.

Only rewrite the stale ARMHF qemu-arm-static dependency on a host that
actually executes ARM64 binaries using the current QEMU packaging. No
system package database changes, symlinks, or bypass of arch-test.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import platform
import shutil
import subprocess

PINNED_QEMU = "qemu-arm-static:qemu-user-static"
ARM64_QEMU = "qemu-aarch64:qemu-user"


def adapt(tree, *, machine=None, which=shutil.which, runner=subprocess.run):
    tree = Path(tree)
    path = tree / "depends"
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing or symlinked pi-gen dependency manifest")
    contents = path.read_text(encoding="utf-8")
    lines = contents.splitlines()
    if lines.count(PINNED_QEMU) != 1:
        raise ValueError("pinned pi-gen QEMU dependency anchor has changed")
    arch = machine if machine is not None else platform.machine()
    # Preserve the exact upstream manifest on native ARM64 hosts, or when
    # older distributions still have pi-gen's original qemu-arm-static.
    if arch == "aarch64" or which("qemu-arm-static"):
        return {"changed": False, "required_emulator": "qemu-arm-static"}
    if not which("qemu-aarch64"):
        raise ValueError("no ARM64 emulator to replace pinned pi-gen requirement")
    try:
        probe = runner(["arch-test", "arm64"], check=False, capture_output=True,
                       text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("ARM64 execution probe unavailable") from error
    if probe.returncode != 0:
        raise ValueError("ARM64 chroot execution must work before pi-gen adaptation")
    path.write_text(contents.replace(PINNED_QEMU, ARM64_QEMU, 1), encoding="utf-8")
    return {"changed": True, "required_emulator": "qemu-aarch64"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pi_gen_tree", type=Path)
    args = parser.parse_args(argv)
    try:
        result = adapt(args.pi_gen_tree)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    print("pi-gen QEMU dependency: " + result["required_emulator"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
