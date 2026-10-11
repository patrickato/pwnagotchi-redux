#!/usr/bin/env python3
"""Verify a transferred Redux release without root, mounts or block devices.

A sidecar manifest/checksum pair is self-consistency evidence, not a signature:
for provenance, compare --trusted-sha256 with a digest from an independent
trusted channel. Never infer successful Pi boot from this verification.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat

from release_manifest import inspect_image

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_MAX_MANIFEST_BYTES = 256 * 1024


def _regular_file(path: Path, *, max_bytes=None):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("release component must be a regular file, not a symlink: " + path.name)
    if max_bytes is not None and info.st_size > max_bytes:
        raise ValueError("release metadata exceeds the maximum allowed size: " + path.name)
    return info


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate release JSON field: " + key)
        result[key] = value
    return result


def _manifest(path):
    _regular_file(path, max_bytes=_MAX_MANIFEST_BYTES)
    try:
        with path.open("rb") as stream:
            report = json.load(stream, object_pairs_hook=_unique_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(
                                   ValueError("non-finite value in release manifest")))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid release JSON manifest") from error
    if not isinstance(report, dict):
        raise ValueError("release manifest must be an object")
    return report


def verify(path, *, expected_profile=None, trusted_sha256=None,
           minimum_bytes=512 * 1024 * 1024):
    image = Path(path)
    if not image.name.endswith(".img.xz"):
        raise ValueError("only compressed Redux .img.xz releases are supported")
    image_meta = _regular_file(image)
    if expected_profile not in (None, "lite", "ab"):
        raise ValueError("expected profile must be lite or ab")
    if trusted_sha256 is not None and not _HEX.fullmatch(trusted_sha256):
        raise ValueError("trusted SHA256 must be 64 lowercase hexadecimal characters")
    manifest = image.with_name(image.name + ".release.json")
    checksum = image.with_name(image.name + ".sha256")
    old_manifest = _regular_file(manifest, max_bytes=_MAX_MANIFEST_BYTES)
    old_checksum = _regular_file(checksum, max_bytes=256)
    with image.open("rb") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("release is currently being published") from error
        record = _manifest(manifest)
        if type(record.get("schema")) is not int or record["schema"] != 1:
            raise ValueError("unsupported release manifest schema")
        if record.get("image") != image.name:
            raise ValueError("release manifest image name mismatch")
        if record.get("hardware_tested") is not False or record.get("boot_tested") is not False:
            raise ValueError("release manifest has unsupported hardware/boot verification claims")
        if record.get("profile_match_verified") is not True:
            raise ValueError("manifest lacks a verified build-profile binding")
        profile = record.get("image_profile")
        if profile not in ("lite", "ab"):
            raise ValueError("missing or invalid release image profile")
        if expected_profile is not None and profile != expected_profile:
            raise ValueError("release profile does not match requested target")
        revisions = record.get("revisions")
        if not isinstance(revisions, dict) or set(revisions) != {"redux", "pi_gen", "nexmon"} or any(
                not isinstance(value, str) or not _REVISION.fullmatch(value)
                for value in revisions.values()):
            raise ValueError("release provenance revisions are incomplete")
        layout = "gpt" if profile == "ab" else "mbr"
        expected_checks = ["xz_stream", "disk_gpt_primary_backup"
                           if profile == "ab" else "disk_mbr_partitions",
                           "partition_filesystem_signatures", "sha256"]
        if record.get("partition_table") != layout or record.get("checks") != expected_checks:
            raise ValueError("release checks or partition layout contradict the profile")
        actual = inspect_image(image, minimum_bytes=minimum_bytes,
                               expected_profile=profile)
        for field in ("sha256", "compressed_bytes", "disk_bytes",
                      "partition_table", "image_profile", "profile_match_verified",
                      "partitions", "partition_filesystems",
                      "captures_filesystem", "captures_label"):
            if record.get(field) != actual[field] or type(record.get(field)) is not type(actual[field]):
                raise ValueError("release manifest does not match image field: " + field)
        _regular_file(checksum, max_bytes=256)
        content = checksum.read_bytes()
        digest = actual["sha256"]
        expected = (digest + "  " + image.name + "\n").encode("ascii")
        if not hmac.compare_digest(content, expected):
            raise ValueError("release checksum sidecar does not match image and manifest")
        if trusted_sha256 is not None and not hmac.compare_digest(digest, trusted_sha256):
            raise ValueError("trusted release SHA256 mismatch")
        for label, location, old in (
                ("image", image, image_meta),
                ("manifest", manifest, old_manifest),
                ("checksum", checksum, old_checksum)):
            now = _regular_file(location)
            if (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns) != (
                    old.st_dev, old.st_ino, old.st_size, old.st_mtime_ns):
                raise ValueError("release " + label + " changed during verification")
        inode = os.fstat(held.fileno())
        if (inode.st_dev, inode.st_ino) != (image_meta.st_dev, image_meta.st_ino):
            raise ValueError("source release image was replaced before verification")
    return {
        "schema": 1,
        "image": image.name,
        "image_profile": profile,
        "sha256": digest,
        "disk_bytes": actual["disk_bytes"],
        "compressed_bytes": actual["compressed_bytes"],
        "manifest_verified": True,
        "checksum_verified": True,
        "partition_layout_verified": True,
        "trusted_digest_matched": trusted_sha256 is not None,
        "image_signature_verified": False,
        "hardware_boot_verified": False,
        "note": "Self-consistency is not authenticity; a separately trusted SHA256 or signature is needed for provenance.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--expected-profile", choices=("lite", "ab"))
    parser.add_argument("--trusted-sha256", help="independently trusted full compressed image SHA256")
    parser.add_argument("--minimum-bytes", type=int, default=512 * 1024 * 1024)
    args = parser.parse_args(argv)
    try:
        report = verify(args.image, expected_profile=args.expected_profile,
                        trusted_sha256=args.trusted_sha256,
                        minimum_bytes=args.minimum_bytes)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
