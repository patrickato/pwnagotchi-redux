"""Verify an exported Pi-gen xz image and write provenance/checksum companions.

This deliberately does not claim that the image booted or was tested on hardware.
It validates xz decompression, the 512-byte disk boot signature, minimum raw
image size and the digest of the exact downloadable compressed bytes.
"""
from __future__ import annotations

import argparse
import itertools
import zlib
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import stat
import tempfile

_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _commit(value):
    if not _COMMIT.fullmatch(value):
        raise ValueError("provenance revision must be a complete lowercase 40-digit Git SHA")
    return value


def _mbr_layout(sector):
    if len(sector) != 512 or sector[510:512] != b"\x55\xaa":
        raise ValueError("missing disk MBR 0x55AA signature")
    records = []
    for idx in range(3):
        entry = sector[446 + idx * 16: 462 + idx * 16]
        kind = entry[4]
        start = int.from_bytes(entry[8:12], "little")
        sectors = int.from_bytes(entry[12:16], "little")
        if start == 0 or sectors == 0 or kind == 0:
            raise ValueError("missing/invalid Pi image partition " + str(idx + 1))
        records.append((kind, start, sectors))
    if records[0][0] not in (0x0B, 0x0C) or any(record[0] != 0x83 for record in records[1:]):
        raise ValueError("MBR partitions must be FAT boot and Linux root/REDUXCAP")
    if any(sector[494:510]):
        raise ValueError("unexpected fourth MBR partition")
    occupied = sorted((start, start + sectors) for _, start, sectors in records)
    for left, right in zip(occupied, occupied[1:]):
        if left[1] > right[0]:
            raise ValueError("overlapping image partitions")
    return records


_GPT_NAMES = ("REDUXCTRL", "REDUXBOOTA", "REDUXBOOTB",
              "REDUXROOTA", "REDUXROOTB", "REDUXCAP")
_GPT_ARRAY_SIZE = 128 * 128
_GPT_TAIL_SIZE = 33 * 512


def _u32(data, offset):
    return int.from_bytes(data[offset:offset + 4], "little")


def _u64(data, offset):
    return int.from_bytes(data[offset:offset + 8], "little")


def _gpt_header(sector, *, current):
    """Validate a standard 128-entry GPT header and its IEEE CRC32."""
    if len(sector) != 512 or sector[:8] != b"EFI PART":
        raise ValueError("GPT header signature missing")
    size = _u32(sector, 12)
    if _u32(sector, 8) != 0x00010000 or not 92 <= size <= 512:
        raise ValueError("unsupported GPT header version or length")
    payload = bytearray(sector[:size])
    original = _u32(payload, 16)
    payload[16:20] = b"\x00" * 4
    if zlib.crc32(payload) != original:
        raise ValueError("GPT header checksum mismatch")
    this_lba = _u64(sector, 24)
    other_lba = _u64(sector, 32)
    entry_lba = _u64(sector, 72)
    if (this_lba != current or _u32(sector, 80) != 128
            or _u32(sector, 84) != 128 or sector[20:24] != bytes(4)):
        raise ValueError("unsupported GPT entry/header geometry")
    return {"this_lba": this_lba, "other_lba": other_lba,
            "first_usable": _u64(sector, 40), "last_usable": _u64(sector, 48),
            "disk_guid": sector[56:72], "entry_lba": entry_lba,
            "entries_crc": _u32(sector, 88)}


def _gpt_layout(prefix):
    """Require the exact A/B partition topology and intact primary GPT CRCs."""
    if len(prefix) < 34 * 512:
        raise ValueError("truncated primary GPT header or entries")
    mbr = prefix[:512]
    if mbr[510:512] != b"\x55\xaa" or mbr[450] != 0xEE or _u32(mbr, 454) != 1:
        raise ValueError("invalid protective GPT MBR")
    head = _gpt_header(prefix[512:1024], current=1)
    if (head["entry_lba"] != 2 or head["first_usable"] < 34
            or head["last_usable"] <= head["first_usable"]
            or head["other_lba"] <= head["last_usable"] + 32):
        raise ValueError("unsupported primary GPT partition bounds")
    entries = prefix[1024:1024 + _GPT_ARRAY_SIZE]
    if zlib.crc32(entries) != head["entries_crc"]:
        raise ValueError("GPT partition array checksum mismatch")
    records = []
    identifiers = set()
    for index in range(128):
        entry = entries[index * 128:(index + 1) * 128]
        if entry[:16] == bytes(16):
            if any(entry):
                raise ValueError("nonempty unused GPT partition record")
            continue
        if index >= 6:
            raise ValueError("unexpected extra GPT partition")
        unique = entry[16:32]
        if not any(unique) or unique in identifiers:
            raise ValueError("GPT partition unique GUID missing or duplicated")
        identifiers.add(unique)
        try:
            name = entry[56:128].decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid GPT partition name") from exc
        start, last = _u64(entry, 32), _u64(entry, 40)
        if (name != _GPT_NAMES[index] or start < head["first_usable"]
                or last < start or last > head["last_usable"]):
            raise ValueError("incorrect Redux A/B GPT partition name or bounds")
        records.append({"gpt_name": name, "start_lba": start,
                        "sector_count": last - start + 1})
    if len(records) != 6:
        raise ValueError("missing Redux A/B GPT partition")
    ordered = sorted(records, key=lambda record: record["start_lba"])
    for first, second in zip(ordered, ordered[1:]):
        if first["start_lba"] + first["sector_count"] > second["start_lba"]:
            raise ValueError("overlapping GPT partitions")
    return records, head, entries


def _gpt_backup(tail, total, primary, entries):
    """Check the mirrored GPT header and entry array at the disk's end."""
    if total % 512 or total < 68 * 512 or len(tail) != _GPT_TAIL_SIZE:
        raise ValueError("GPT disk size is not sector aligned")
    last_lba = total // 512 - 1
    if primary["other_lba"] != last_lba or primary["last_usable"] >= last_lba - 32:
        raise ValueError("GPT backup header does not match disk length")
    backup = _gpt_header(tail[-512:], current=last_lba)
    if (backup["other_lba"] != 1 or backup["entry_lba"] != last_lba - 32
            or backup["first_usable"] != primary["first_usable"]
            or backup["last_usable"] != primary["last_usable"]
            or backup["disk_guid"] != primary["disk_guid"]
            or backup["entries_crc"] != primary["entries_crc"]):
        raise ValueError("primary and backup GPT headers disagree")
    if tail[:_GPT_ARRAY_SIZE] != entries:
        raise ValueError("backup GPT partition entries differ from primary")



def _filesystem_checks(partitions, table):
    """Bounded on-disk signatures, NOT fsck, bootability or file contents.

    FAT16 is used by the A/B selector, FAT32 by boot partitions, and ext4
    by roots/captures. Bounds are measured relative to their own partition;
    a valid signature in the next partition must never satisfy this check.
    """
    expected = (
        [("fat32", None), ("ext4", None), ("ext4", "REDUXCAP")]
        if table == "mbr" else
        [("fat16", "REDUXCTRL"), ("fat32", "REDUXBOOTA"),
         ("fat32", "REDUXBOOTB"), ("ext4", "REDUXROOTA"),
         ("ext4", "REDUXROOTB"), ("ext4", "REDUXCAP")]
    )
    if len(partitions) != len(expected):
        raise ValueError("unexpected number of image filesystems")
    checks = {}
    for index, (partition, (kind, label)) in enumerate(zip(partitions, expected), 1):
        base = partition["start_lba"] * 512
        limit = base + partition["sector_count"] * 512
        if kind.startswith("fat"):
            fields = {"boot_signature": (base + 510, 2),
                      "fat_type": (base + (54 if kind == "fat16" else 82), 5)}
            if label:
                fields["label"] = (base + (43 if kind == "fat16" else 71), 11)
        else:
            fields = {"ext4_magic": (base + 1024 + 0x38, 2)}
            if label:
                fields["label"] = (base + 1024 + 0x78, 16)
        for field, (offset, length) in fields.items():
            if offset < base or offset + length > limit:
                raise ValueError(f"partition {index} is too short for its {kind} header")
            checks[(index, field)] = (offset, length)
    return expected, checks


def _check_filesystem_signatures(expected, observed):
    """Validate sampled bytes from each partition (no mount or full fsck)."""
    results = []
    for index, (kind, label) in enumerate(expected, 1):
        if kind.startswith("fat"):
            matched = (observed[(index, "boot_signature")] == b"\x55\xaa"
                       and observed[(index, "fat_type")] == kind.upper().encode("ascii"))
        else:
            matched = observed[(index, "ext4_magic")] == b"\x53\xef"
        if not matched:
            raise ValueError(f"partition {index} has no valid {kind} filesystem signature")
        if label is not None:
            stored = observed[(index, "label")].rstrip(b"\x00 ").decode("ascii", "replace")
            if stored != label:
                raise ValueError(f"partition {index} filesystem label mismatch: expected {label}")
        results.append({"partition": index, "format": kind, "label": label,
                        "signature_observed": True,
                        "filesystem_checked": False})
    return results


def inspect_image(path, *, minimum_bytes=512 * 1024 * 1024, expected_profile=None):
    """Stream-check a disk, optionally enforcing the operator-selected profile.

    A/B signing configuration must never silently publish a Lite image, or
    vice versa. Unspecified profiles preserve standalone inspection behavior.
    """
    if expected_profile not in (None, "lite", "ab"):
        raise ValueError("expected image profile must be lite or ab")
    path = Path(path)
    meta = path.lstat()
    if not stat.S_ISREG(meta.st_mode) or not path.name.endswith(".img.xz"):
        raise ValueError("artifact must be a regular *.img.xz file, not a symlink")
    if minimum_bytes < 512:
        raise ValueError("minimum image size must be >=512 bytes")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)

    total = 0
    tail = b""
    try:
        with lzma.open(path, "rb") as reader:
            prefix = reader.read(34 * 512)
            if len(prefix) < 512 or prefix[510:512] != b"\x55\xaa":
                raise ValueError("missing disk MBR 0x55AA signature")
            if prefix[450] == 0xEE:
                table = "gpt"
                partitions, primary, entries = _gpt_layout(prefix)
            else:
                table = "mbr"
                mbr = _mbr_layout(prefix[:512])
                partitions = [
                    {"mbr_type": kind, "start_lba": start, "sector_count": sectors}
                    for kind, start, sectors in mbr
                ]
            actual_profile = "ab" if table == "gpt" else "lite"
            if expected_profile is not None and actual_profile != expected_profile:
                raise ValueError(
                    f"release profile mismatch: expected {expected_profile}, "
                    f"but image contains {actual_profile} ({table}) layout")
            expected, probes = _filesystem_checks(partitions, table)
            observed = {name: b"" for name in probes}
            # Small bounded reads for every filesystem's boot/superblock evidence.
            # Consume prefix too; it can contain the boot-sector fields.
            for block in itertools.chain((prefix,), iter(lambda: reader.read(1024 * 1024), b"")):
                start = total
                end = start + len(block)
                for name, (offset, size) in probes.items():
                    prev = observed[name]
                    region_start = max(start, offset + len(prev))
                    region_end = min(end, offset + size)
                    if region_start < region_end:
                        observed[name] = prev + block[region_start - start:region_end - start]
                if table == "gpt":
                    tail = (tail + block)[-_GPT_TAIL_SIZE:]
                total = end
    except (lzma.LZMAError, EOFError, OSError) as error:
        raise ValueError(f"corrupt or truncated xz image: {type(error).__name__}") from error
    if total < minimum_bytes:
        raise ValueError(f"decompressed disk image too small: {total} bytes")
    if total % 512:
        raise ValueError("decompressed disk image not sector aligned")
    if any((item["start_lba"] + item["sector_count"]) * 512 > total
           for item in partitions):
        raise ValueError("image partition extends beyond decompressed disk")
    if table == "gpt":
        _gpt_backup(tail, total, primary, entries)
    evidence = _check_filesystem_signatures(expected, observed)
    if path.lstat().st_size != meta.st_size or path.lstat().st_mtime_ns != meta.st_mtime_ns:
        raise ValueError("compressed image changed while being validated")
    return {
        "sha256": digest.hexdigest(),
        "compressed_bytes": meta.st_size, "disk_bytes": total,
        "partition_table": table, "image_profile": actual_profile,
        "profile_match_verified": expected_profile is not None,
        "partitions": partitions,
        "partition_filesystems": evidence,
        "captures_filesystem": "ext4", "captures_label": "REDUXCAP",
    }


def _write_private_atomic(target: Path, data: bytes):
    # Release metadata is public, but atomic publication prevents half manifests.
    fd, temp = tempfile.mkstemp(prefix=".redux-manifest-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp, 0o644)
        os.replace(temp, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def publish(path, revision, pi_gen_revision, nexmon_revision, *,
            minimum_bytes=512 * 1024 * 1024, expected_profile=None):
    image = Path(path)
    revs = {
        "redux": _commit(revision),
        "pi_gen": _commit(pi_gen_revision),
        "nexmon": _commit(nexmon_revision),
    }
    info = inspect_image(image, minimum_bytes=minimum_bytes,
                         expected_profile=expected_profile)
    record = {
        "schema": 1,
        "image": image.name,
        "revisions": revs,
        **info,
        "checks": ["xz_stream", ("disk_gpt_primary_backup" if info["partition_table"] == "gpt" else "disk_mbr_partitions"), "partition_filesystem_signatures", "sha256"],
        "hardware_tested": False,
        "boot_tested": False,
        "note": "File-integrity checks only. ARM64 boot, partition mounts, radio and capture require physical validation.",
    }
    manifest = image.with_name(image.name + ".release.json")
    checksum = image.with_name(image.name + ".sha256")
    _write_private_atomic(
        manifest, (json.dumps(record, sort_keys=True, indent=2) + "\n").encode("utf-8")
    )
    _write_private_atomic(
        checksum, (info["sha256"] + "  " + image.name + "\n").encode("ascii")
    )
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--pi-gen-revision", required=True)
    parser.add_argument("--nexmon-revision", required=True)
    parser.add_argument("--minimum-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--expected-profile", choices=("lite", "ab"),
                        help="fail closed if exported disk layout differs from selected build")
    args = parser.parse_args(argv)
    try:
        record = publish(
            args.artifact, args.revision, args.pi_gen_revision, args.nexmon_revision,
            minimum_bytes=args.minimum_bytes, expected_profile=args.expected_profile,
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
