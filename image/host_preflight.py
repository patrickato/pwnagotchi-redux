"""Read-only Linux build-host qualification for Redux Pi 4/5 ARM64 images.

The probe never downloads, mounts, installs, builds, modifies source checkouts,
or claims that an image booted. It checks prerequisites, not build success.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

GIB = 1024 ** 3
REQUIRED_TOOLS = (
    "git", "python3", "realpath", "install", "tar", "quilt", "parted",
    "rsync", "losetup", "blkid", "mount", "umount", "debootstrap",
    "mkfs.ext4", "mkfs.vfat", "xz", "zip", "zerofree", "arch-test",
    "findmnt", "gpg", "pigz", "curl", "file", "xxd", "bc", "mkdosfs",
)


def item(status, detail, **evidence):
    if status not in ("ok", "blocker", "unknown", "advisory"):
        raise ValueError("invalid preflight status")
    return {"status": status, "detail": detail, "evidence": evidence}


def nearest_parent(path: Path) -> Path:
    current = path
    while not current.exists():
        if current.parent == current:
            raise ValueError("cannot find an existing workspace ancestor")
        current = current.parent
    return current


def report(workspace, *, uid=None, system=None, machine=None,
           which=shutil.which, runner=subprocess.run,
           statvfs=os.statvfs, fs_type=None, available_bytes=None):
    """Evaluate without writes. Injectable probes make failure paths testable."""
    root = Path(os.path.abspath(workspace))
    owner = os.geteuid() if uid is None else uid
    os_name = platform.system() if system is None else system
    arch = platform.machine() if machine is None else machine
    checks = {}
    checks["platform"] = item("ok" if os_name == "Linux" else "blocker",
                              "Linux host observed" if os_name == "Linux" else "Linux host required",
                              system=os_name, architecture=arch)
    checks["privilege"] = item("ok" if owner == 0 else "blocker",
                               "root for chroot and loop mounts" if owner == 0 else
                               "full image build requires root (sudo)", euid=owner)
    if " " in str(root) or "\n" in str(root):
        checks["workspace_path"] = item("blocker", "pi-gen build paths cannot contain whitespace", path=str(root))
    elif root.exists() or root.is_symlink():
        checks["workspace_path"] = item("blocker", "build directory already exists; choose a fresh path", path=str(root))
    else:
        checks["workspace_path"] = item("ok", "fresh build directory path", path=str(root))
    try:
        ancestor = nearest_parent(root)
        if fs_type is None:
            query = runner(["findmnt", "-T", str(ancestor), "-n", "-o", "FSTYPE"],
                           capture_output=True, text=True, timeout=5, check=False)
            filesystem = query.stdout.strip() if query.returncode == 0 else None
        else:
            filesystem = fs_type
        if not filesystem:
            checks["workspace_filesystem"] = item("unknown", "cannot confirm workspace filesystem", ancestor=str(ancestor))
        elif filesystem == "ext4":
            checks["workspace_filesystem"] = item("ok", "workspace on ext4", ancestor=str(ancestor), fstype=filesystem)
        else:
            checks["workspace_filesystem"] = item("blocker", "workspace must be on ext4, not this filesystem",
                                                   ancestor=str(ancestor), fstype=filesystem)
        disk_stats = statvfs(ancestor) if available_bytes is None else None
        free = (disk_stats.f_bavail * disk_stats.f_frsize) if disk_stats else available_bytes
        if free < 20 * GIB:
            checks["disk_space"] = item("blocker", "less than 20 GiB free; insufficient build scratch", free_bytes=free)
        elif free < 30 * GIB:
            checks["disk_space"] = item("advisory", "under recommended 30 GiB free", free_bytes=free)
        else:
            checks["disk_space"] = item("ok", "at least 30 GiB free", free_bytes=free)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        checks.setdefault("workspace_filesystem", item("unknown", "filesystem query failed", error=type(error).__name__))
        checks["disk_space"] = item("unknown", "workspace free-space query failed", error=type(error).__name__)

    # Ubuntu 26.04 and newer ship qemu-user as static executables named
    # qemu-aarch64, with registration in qemu-user-binfmt. Older Ubuntu and
    # Debian builds provide qemu-aarch64-static. Accept either spelling,
    # while arch-test below still proves that ARM64 chroot execution works.
    emulators = () if arch == "aarch64" else ("qemu-aarch64-static", "qemu-aarch64")
    emulator = next((command for command in emulators if which(command)), None)
    missing = [command for command in REQUIRED_TOOLS if not which(command)]
    if emulators and emulator is None:
        missing.append("qemu-aarch64-static or qemu-aarch64")
    checks["dependencies"] = item(
        "blocker" if missing else "ok",
        "missing required host tools" if missing else "required host tools found",
        missing=missing, examined=list(REQUIRED_TOOLS + emulators),
        qemu_emulator=emulator,
    )
    if os_name != "Linux":
        checks["arm64_execution"] = item("blocker", "arm64 chroot requires Linux")
    elif not which("arch-test"):
        checks["arm64_execution"] = item("blocker", "arch-test unavailable; cannot qualify arm64 chroot")
    else:
        try:
            proc = runner(["arch-test", "arm64"], capture_output=True, text=True,
                          timeout=8, check=False)
            checks["arm64_execution"] = item(
                "ok" if proc.returncode == 0 else "blocker",
                "ARM64 userspace execution verified" if proc.returncode == 0 else
                "ARM64 userspace execution failed; check qemu binfmt registration",
                host_arch=arch, returncode=proc.returncode)
        except (OSError, subprocess.TimeoutExpired):
            checks["arm64_execution"] = item("unknown", "ARM64 execution probe unavailable")
    blockers = [key for key, value in checks.items() if value["status"] == "blocker"]
    unknown = [key for key, value in checks.items() if value["status"] == "unknown"]
    return {"schema": 1, "overall": "blocked" if blockers else "unverified" if unknown else "host_checks_ok",
            "build_ready": not blockers and not unknown, "workspace": str(root),
            "checks": checks, "blockers": blockers, "unverified": unknown,
            "network_sources_fetched": False, "image_built": False,
            "physical_boot_verified": False,
            "note": "Host prerequisites only; not evidence of a successful build or device boot"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    result = report(args.workspace)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["build_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
