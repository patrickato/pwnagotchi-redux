"""Verify an exported Pi-gen xz image and write provenance/checksum companions.

This deliberately does not claim that the image booted or was tested on hardware.
It validates xz decompression, the 512-byte disk boot signature, minimum raw
image size and the digest of the exact downloadable compressed bytes.
"""
from __future__ import annotations

import argparse
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


def inspect_image(path, *, minimum_bytes=512 * 1024 * 1024):
    """Stream two bounded passes; no root, mounts, guest boot, or large RAM."""
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
    prefix = b""
    try:
        with lzma.open(path, "rb") as reader:
            while block := reader.read(1024 * 1024):
                if len(prefix) < 512:
                    prefix += block[:512 - len(prefix)]
                total += len(block)
    except (lzma.LZMAError, EOFError, OSError) as error:
        raise ValueError(f"corrupt or truncated xz image: {type(error).__name__}") from error
    if total < minimum_bytes:
        raise ValueError(f"decompressed disk image too small: {total} bytes")
    if prefix[510:512] != b"\x55\xaa":
        raise ValueError("missing disk MBR/protective-MBR 0x55AA signature")
    if path.lstat().st_size != meta.st_size or path.lstat().st_mtime_ns != meta.st_mtime_ns:
        raise ValueError("compressed image changed while being validated")
    return {"sha256": digest.hexdigest(),
            "compressed_bytes": meta.st_size, "disk_bytes": total}


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
            minimum_bytes=512 * 1024 * 1024):
    image = Path(path)
    revs = {
        "redux": _commit(revision),
        "pi_gen": _commit(pi_gen_revision),
        "nexmon": _commit(nexmon_revision),
    }
    info = inspect_image(image, minimum_bytes=minimum_bytes)
    record = {
        "schema": 1,
        "image": image.name,
        "revisions": revs,
        **info,
        "checks": ["xz_stream", "disk_boot_signature", "sha256"],
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
    args = parser.parse_args(argv)
    try:
        record = publish(
            args.artifact, args.revision, args.pi_gen_revision, args.nexmon_revision,
            minimum_bytes=args.minimum_bytes,
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
