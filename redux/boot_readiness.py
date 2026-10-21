"""Read-only first-boot acceptance evidence for standalone Redux images.

No remounts, filesystem writes, systemd mutations, firmware changes, radio
operations or privileged code paths. Software evidence alone is never a
physical Pi 4/Pi 5 qualification or proof of capture functionality.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

from .display.kiosk import dashboard_probe

_UNITS = {
    "redux.service": "active",
    "redux-live.service": "active",
    "redux-capture-ingest.timer": "active",
    "bettercap.service": "masked",
}
_MANIFESTS = (
    "redux-revision", "sources.sh", "packages.tsv",
    "nexmon-driver.sha256", "nexmon-firmware.sha256",
)
_OCTAL = re.compile(r"\\([0-7]{3})")


def _unescape(value: str) -> str:
    """Decode the octal escapes used for mountinfo path separators."""
    return _OCTAL.sub(lambda match: chr(int(match.group(1), 8)), value)


def parse_mountinfo(contents: str) -> dict:
    """Use complete mountinfo records, not `mount` substring matches.

    Last matching mountpoint wins: this is the top mount when paths are stacked.
    A broken line is ignored rather than converted into a healthy mount.
    """
    mounts = {}
    for line in contents.splitlines():
        lhs, separator, rhs = line.partition(" - ")
        if not separator:
            continue
        left, right = lhs.split(), rhs.split()
        if len(left) < 6 or len(right) < 3:
            continue
        mountpoint = _unescape(left[4])
        if not mountpoint.startswith("/"):
            continue
        mounts[mountpoint] = {
            "device_major_minor": left[2], "filesystem": right[0],
            "source": _unescape(right[1]), "options": left[5].split(","),
        }
    return mounts


def _read(path: Path, *, limit=8192) -> str | None:
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
            if len(data) > limit:
                return None  # Truncated mount evidence must never be accepted as complete.
            return data.decode("utf-8", "replace").strip("\x00\n\r ")
    except OSError:
        return None


def finding(status, detail, remediation="", **evidence):
    if status not in {"ok", "action", "unknown"}:
        raise ValueError("invalid diagnosis")
    return {"status": status, "detail": detail, "remediation": remediation,
            "evidence": evidence}


def mount_check(mounts, path, *, fs, mode, label=None, label_path=Path("/dev/disk/by-label/REDUXCAP"),
                realpath=os.path.realpath):
    record = mounts.get(path)
    if record is None:
        return finding("action", f"{path} is not a mountpoint",
                       "Inspect partition discovery, fstab/generator and journalctl -b")
    evidence = {"filesystem": record["filesystem"], "source": record["source"],
                "options": record["options"]}
    if record["filesystem"] != fs or mode not in record["options"]:
        return finding("action", f"{path} is not {mode} {fs}",
                       "Inspect the mount unit, block filesystem and kernel logs", **evidence)
    if label:
        try:
            if not label_path.is_symlink():
                return finding("unknown", f"{label} label symlink not observable",
                               "Inspect lsblk -f and /dev/disk/by-label", **evidence)
            observed = realpath(record["source"])
            labeled = realpath(str(label_path))
            if observed != labeled:
                return finding("action", f"{path} is not backed by {label}",
                               "Inspect partition labels and device-to-mount mapping",
                               expected_device=labeled, **evidence)
        except OSError:
            return finding("unknown", f"{label} device could not be resolved",
                           "Inspect udev/disk label creation", **evidence)
    return finding("ok", f"{path} mounted {mode} as {fs}; provenance checked" if label else
                   f"{path} mounted {mode} as {fs}", **evidence)


def service_probe(unit, expected, *, runner=subprocess.run):
    """Never starts units. Query only explicitly named Redux-owned units."""
    if unit not in _UNITS or expected != _UNITS[unit]:
        raise ValueError("unsupported service expectation")
    try:
        proc = runner(["systemctl", "show", "--no-pager", "--property=LoadState,ActiveState,UnitFileState",
                       unit], text=True, capture_output=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return finding("unknown", f"systemd state unavailable for {unit}",
                       "Run on the booted Redux system with systemd")
    if proc.returncode:
        return finding("unknown", f"systemctl could not query {unit}",
                       "Inspect systemd availability on this boot")
    props = dict(line.split("=", 1) for line in proc.stdout[:4096].splitlines()
                 if "=" in line)
    if not {"LoadState", "ActiveState", "UnitFileState"} <= props.keys():
        return finding("unknown", f"incomplete systemd data for {unit}")
    observed = {k: props[k] for k in ("LoadState", "ActiveState", "UnitFileState")}
    if expected == "masked":
        ok = props["UnitFileState"] == "masked" and props["ActiveState"] != "active"
    else:
        ok = props["LoadState"] == "loaded" and props["ActiveState"] == "active"
    return finding("ok" if ok else "action",
                   f"{unit} has expected state" if ok else f"{unit} is not {expected}",
                   "Inspect systemctl status and journalctl -b for this unit" if not ok else "",
                   **observed)


def snapshot(*, mountinfo=Path("/proc/self/mountinfo"),
             model_file=Path("/proc/device-tree/model"),
             manifests=Path("/usr/share/redux"),
             label_path=Path("/dev/disk/by-label/REDUXCAP"),
             machine=None, release=None, service=service_probe,
             dashboard=dashboard_probe, realpath=os.path.realpath) -> dict:
    """Only observed states are marked OK; absent evidence is not a success."""
    arch = platform.machine() if machine is None else machine
    kernel = platform.release() if release is None else release
    board = _read(model_file)
    checks = {}
    checks["architecture"] = finding(
        "ok" if arch == "aarch64" and sys.maxsize > 2**32 else "action",
        "ARM64 runtime observed" if arch == "aarch64" and sys.maxsize > 2**32 else
        "Not confirmed as ARM64 userspace and kernel",
        "Verify flashed image target and 64-bit OS" if arch != "aarch64" else "",
        machine=arch, kernel=kernel, python_bits=64 if sys.maxsize > 2**32 else 32)
    if board is None:
        checks["board"] = finding("unknown", "Board model unavailable", "Inspect device tree")
    elif "Raspberry Pi 4" in board or "Raspberry Pi 5" in board:
        checks["board"] = finding("ok", "Supported Pi model reported by device tree",
                                  model=board[:120])
    else:
        checks["board"] = finding("action", "Device tree does not identify Pi 4 or Pi 5",
                                  "Check target board and hardware profile", model=board[:120])

    text = _read(mountinfo, limit=512 * 1024)
    if text is None:
        for key in ("root", "root_lower", "boot_firmware", "captures"):
            checks[key] = finding("unknown", "Kernel mount inventory unavailable")
    else:
        mounts = parse_mountinfo(text)
        checks["root"] = mount_check(mounts, "/", fs="overlay", mode="rw")
        checks["root_lower"] = mount_check(mounts, "/media/root-ro", fs="ext4", mode="ro")
        checks["boot_firmware"] = mount_check(mounts, "/boot/firmware", fs="vfat", mode="ro")
        checks["captures"] = mount_check(mounts, "/captures", fs="ext4", mode="rw",
                                           label="REDUXCAP", label_path=label_path,
                                           realpath=realpath)
    observed = {name: (manifests / name).is_file() for name in _MANIFESTS}
    absent = [name for name, present in observed.items() if not present]
    checks["image_manifest"] = finding(
        "ok" if not absent else "action",
        "Image revision and firmware evidence present (not hash-verified)" if not absent else
        "Missing baked image revision or Nexmon evidence",
        "Inspect image build/staging logs" if absent else "",
        present=observed)
    for unit, expectation in _UNITS.items():
        checks["unit:" + unit] = service(unit, expectation)
    web = dashboard(8080)
    checks["dashboard"] = finding(
        "ok" if web.get("ok") else "unknown",
        "Redux loopback Doctor JSON observed" if web.get("ok") else
        "No confirmed Redux Doctor HTTP response on loopback",
        "Inspect redux-live logs and /etc/redux/live.toml" if not web.get("ok") else "",
        probe_reason=str(web.get("reason", ""))[:120])
    action = sum(c["status"] == "action" for c in checks.values())
    unknown = sum(c["status"] == "unknown" for c in checks.values())
    overall = "action" if action else "unverified" if unknown else "software_checks_ok"
    return {"schema": 1, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "overall": overall, "counts": {"ok": len(checks) - action - unknown,
                                            "action": action, "unknown": unknown},
            "checks": checks, "physical_pi_qualified": False,
            "image_boot_and_radio_qualified": False,
            "note": "Read-only software evidence; manual Pi boot, pixels, radio and power-loss tests remain"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Redux first-boot diagnosis")
    parser.add_argument("--strict", action="store_true",
                        help="treat unobservable/unknown checks as nonzero exit (3)")
    args = parser.parse_args(argv)
    report = snapshot()
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["counts"]["action"]:
        return 2
    return 3 if args.strict and report["counts"]["unknown"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
