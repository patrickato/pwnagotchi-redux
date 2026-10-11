"""Pinned identity of the writable REDUXCAP mount, shared by live and ingest.

Read-only /proc mount observation. None means unverified or unavailable.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat


def capture_mount_snapshot(*, mountinfo=Path("/proc/self/mountinfo"),
                           mountpoint=Path("/captures")):
    """Return a pinned mount identity; never confuse a remount with continuity.

    Filesystem paths alone are insufficient: /captures may be unmounted and
    replaced between supervisor ticks, leaving owner.lock on the old inode.
    Require a real ext4 mount, preserve its kernel mount ID and underlying
    device, and verify the root directory inode remains unchanged. A failed
    probe always returns None (fail closed).
    """
    try:
        if not os.path.ismount(mountpoint) or mountpoint.is_symlink():
            return None
        evidence = None
        for line in mountinfo.read_text(encoding="utf-8").splitlines():
            left, separator, right = line.partition(" - ")
            if not separator:
                continue
            fields, fs = left.split(), right.split()
            if len(fields) < 6 or len(fs) < 3:
                continue
            if fields[4] != str(mountpoint):
                continue
            # Linux reports kernel mount IDs for individual mount instances.
            # A new mount on the same disk still gets a new identity.
            if not fields[0].isdigit() or fs[0] != "ext4":
                return None
            evidence = (int(fields[0]), fields[2], fs[0])
        if evidence is None:
            return None
        info = mountpoint.stat()
        if not stat.S_ISDIR(info.st_mode):
            return None
        return evidence + (info.st_dev, info.st_ino)
    except (OSError, UnicodeError, ValueError):
        return None




