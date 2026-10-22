"""Storage tiering — use all the storage that's present, not just the SD card.

The primary capture mount stays exactly as strict as it is (ext4 + rw, inode-lock
capable — see storage_mount.py); that gate protects owner.lock continuity and is
kept. What this adds is the scaling the device was missing: when an external USB
disk is present it becomes a BULK tier for large pcapng overflow, and when a
network mount (the NAS) is present it becomes an ARCHIVE tier for long-term
offload. SD-only still works; each added volume adds capacity instead of idling.

Pure decision engine — no OS calls — so it unit-tests with no hardware. The live
layer builds the volume list (findmnt / statvfs) and the rotation/offload paths
consume the plan. Every decision carries a reason (glass-box).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, List, Optional

# Filesystems safe for the primary (inode semantics owner.lock relies on).
_PRIMARY_FS = frozenset({"ext4"})
# Filesystems fine for bulk pcapng (no locking needed) — removable media included.
_BULK_FS = frozenset({"ext4", "ext3", "exfat", "vfat", "fat", "f2fs", "btrfs", "xfs"})
# Network filesystems → the NAS archive tier.
_NETWORK_FS = frozenset({"nfs", "nfs4", "cifs", "smb3", "smbfs", "9p"})


class Tier(str, Enum):
    PRIMARY = "primary"     # live capture: SD root, ext4, rw, lock-capable
    BULK = "bulk"           # overflow / large pcapng: external USB disk
    ARCHIVE = "archive"     # long-term offload: NAS / network mount


@dataclass(frozen=True)
class StorageVolume:
    mountpoint: str
    fstype: str
    rw: bool = True
    network: bool = False
    avail_bytes: Optional[int] = None


@dataclass
class StoragePlan:
    tiers: dict                                     # Tier -> mountpoint (or None)
    reasons: dict = field(default_factory=dict)     # Tier -> why (glass-box)
    warnings: list = field(default_factory=list)

    def target(self, tier: Tier) -> Optional[str]:
        return self.tiers.get(tier)


def parse_findmnt(text) -> List[StorageVolume]:
    """Pure parser of `findmnt -J -o TARGET,FSTYPE,OPTIONS,SOURCE` output.

    `findmnt -J` emits a TREE: the USB bulk disk and the NAS mount appear as
    `children` of `/`, not as top-level rows. Walk the tree so those tiers are
    actually seen (a flat top-level-only read would make the whole feature no-op)."""
    out: List[StorageVolume] = []
    try:
        data = json.loads(text) if isinstance(text, str) else text
    except (ValueError, TypeError):
        return out
    roots = data.get("filesystems", []) if isinstance(data, dict) else []
    if not isinstance(roots, list):
        return out
    stack = list(roots)
    seen_targets: set = set()
    budget = 0
    while stack and budget < 4096:
        budget += 1
        row = stack.pop()
        if not isinstance(row, dict):
            continue
        children = row.get("children")
        if isinstance(children, list):
            stack.extend(children)
        target = row.get("target")
        fstype = str(row.get("fstype", "")).lower()
        if not isinstance(target, str) or not target or not fstype:
            continue
        if target in seen_targets:
            continue
        seen_targets.add(target)
        opts = str(row.get("options", ""))
        source = str(row.get("source", ""))
        rw = "rw" in opts.split(",")
        network = fstype in _NETWORK_FS or ":" in source.split("/")[0]
        out.append(StorageVolume(mountpoint=target, fstype=fstype, rw=rw, network=network))
    return out


def plan_storage_tiers(volumes: Iterable[StorageVolume], *,
                       primary_mount: str = "/captures") -> StoragePlan:
    """Assign each present volume to a storage tier. SD-only is complete; a USB
    disk adds BULK overflow; a network mount adds NAS ARCHIVE offload."""
    volumes = list(volumes)
    tiers = {Tier.PRIMARY: None, Tier.BULK: None, Tier.ARCHIVE: None}
    reasons: dict = {}
    warnings: list = []

    primary = next((v for v in volumes if v.mountpoint == primary_mount
                    and v.fstype in _PRIMARY_FS and v.rw and not v.network), None)
    if primary is not None:
        tiers[Tier.PRIMARY] = primary.mountpoint
        reasons[Tier.PRIMARY] = f"primary: live capture on {primary.mountpoint} ({primary.fstype}, rw)"
    else:
        warnings.append(
            f"no lock-capable ext4 rw mount at {primary_mount} — live capture cannot "
            f"persist safely until it is present")

    # Archive tier: a writable network mount (the NAS).
    archive = next((v for v in volumes if v.network and v.rw
                    and v.mountpoint != primary_mount), None)
    if archive is not None:
        tiers[Tier.ARCHIVE] = archive.mountpoint
        reasons[Tier.ARCHIVE] = (f"archive: long-term offload to the NAS at "
                                 f"{archive.mountpoint} ({archive.fstype})")

    # Bulk tier: a writable local extra disk (USB stick/SSD), any removable fs.
    bulk = next((v for v in volumes if not v.network and v.rw
                 and v.fstype in _BULK_FS
                 and v.mountpoint not in (primary_mount, tiers[Tier.ARCHIVE])), None)
    if bulk is not None:
        tiers[Tier.BULK] = bulk.mountpoint
        reasons[Tier.BULK] = (f"bulk: large pcapng overflow on {bulk.mountpoint} "
                              f"({bulk.fstype})")

    if tiers[Tier.PRIMARY] and not tiers[Tier.BULK] and not tiers[Tier.ARCHIVE]:
        warnings.append("SD-only: add a USB disk for bulk overflow or mount the NAS "
                        "for archive offload to use more storage")
    return StoragePlan(tiers, reasons, warnings)
