"""Disable upstream Pi OS first-boot root resizing for Redux fixed partitions.

pi-gen stage2 adds Bookworm firstboot init and enables resize2fs_once.
Redux keeps REDUXCAP after root; neither stock resize path may run.
"""
from __future__ import annotations

import argparse
from pathlib import Path

_INIT_RESIZERS = frozenset((
    "init=/usr/lib/raspberrypi-sys-mods/firstboot",
    "init=/usr/lib/raspi-config/init_resize.sh",
))


def disable(root):
    root = Path(root)
    boot = root / "boot/firmware/cmdline.txt"
    if not boot.is_file() or boot.is_symlink():
        raise ValueError("pi-gen cmdline.txt must be a regular file")
    data = boot.read_text(encoding="utf-8")
    if len(data) > 8192 or "\x00" in data or "\n" in data.strip("\r\n"):
        raise ValueError("unexpected boot command line")
    words = data.split()
    resize = [word for word in words if word in _INIT_RESIZERS]
    if len(resize) != 1:
        raise ValueError("expected exactly one stock pi-gen firstboot resize init")
    remaining = [word for word in words if word not in _INIT_RESIZERS]
    if len([word for word in remaining if word.startswith("root=")]) != 1:
        raise ValueError("boot command line requires one root selector")
    if any(word.startswith("init=") or "firstboot" in word.lower()
           or "resize" in word.lower() for word in remaining):
        raise ValueError("unexpected first-boot initializer or resize instruction")
    legacy = root / "etc/init.d/resize2fs_once"
    if legacy.is_symlink() or not legacy.is_file():
        raise ValueError("expected pi-gen resize2fs_once init script")
    service = root / "etc/systemd/system/resize2fs_once.service"
    if service.exists() or service.is_symlink():
        raise ValueError("resize2fs_once systemd unit unexpectedly already staged")
    # Check everything before mutation. The stage masks the legacy unit.
    boot.write_text(" ".join(remaining) + "\n", encoding="utf-8")
    return {"removed_init": resize[0], "resize2fs_once_needs_mask": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    try:
        report = disable(args.root)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    print("Redux partition safety: removed " + report["removed_init"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
