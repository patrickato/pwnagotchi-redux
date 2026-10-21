#!/usr/bin/env python3
"""Package an existing pi-gen image artifact with a verifiable provenance manifest.

This is intentionally a packaging step, not proof that the image boots on Pi.
It never builds, mounts, flashes, extracts, or modifies a source image.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

SUFFIXES = (".img.xz", ".img.zst", ".img", ".zip")
REVISION = re.compile(r"^[0-9a-fA-F]{40}$")


def image_candidates(deploy: Path):
    if not deploy.is_dir() or deploy.is_symlink():
        raise ValueError("deployment folder is missing or a symbolic link")
    return sorted(
        p for p in deploy.iterdir()
        if p.is_file() and not p.is_symlink()
        and any(p.name.endswith(suffix) for suffix in SUFFIXES)
    )


def pick_image(deploy: Path, requested=None):
    candidates = image_candidates(deploy)
    if requested:
        image = deploy / requested
        if (Path(requested).name != requested or image not in candidates):
            raise ValueError("requested image is not a regular file inside deploy")
        return image
    if len(candidates) != 1:
        raise ValueError(
            f"expected one image in {deploy}, found {len(candidates)}; "
            "pass --image FILENAME to resolve multiple build outputs"
        )
    return candidates[0]


def sha256_file(file):
    h = hashlib.sha256()
    with Path(file).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package_image(deploy, output, revision, image=None):
    deploy, output = Path(deploy), Path(output)
    if not REVISION.fullmatch(revision):
        raise ValueError("revision must be the full 40-character commit SHA")
    source = pick_image(deploy, image)
    if source.stat().st_size == 0:
        raise ValueError("refusing empty image artifact")
    if output.is_symlink():
        raise ValueError("release output directory may not be a symbolic link")
    output.mkdir(parents=True, exist_ok=True)
    if output.resolve() == deploy.resolve():
        raise ValueError("release output must differ from image deployment folder")
    target = output / source.name
    manifest_path = output / "manifest.json"
    checksum_path = output / (source.name + ".sha256")
    for dst in (target, manifest_path, checksum_path):
        if dst.exists() or dst.is_symlink():
            raise FileExistsError(f"release already has {dst.name}; use a fresh output folder")

    fd, partial = tempfile.mkstemp(prefix=".image-", dir=output)
    try:
        with os.fdopen(fd, "wb") as stream, source.open("rb") as inp:
            shutil.copyfileobj(inp, stream, 4 * 1024 * 1024)
            stream.flush()
            os.fsync(stream.fileno())
        if sha256_file(source) != sha256_file(partial):
            raise RuntimeError("copy verification mismatch (image changed during packaging)")
        os.replace(partial, target)
    finally:
        if os.path.exists(partial):
            os.unlink(partial)

    digest = sha256_file(target)
    checksum_path.write_text(f"{digest}  {target.name}\n", encoding="ascii")
    manifest = {
        "schema": 1,
        "project": "pwnagotchi-redux",
        "source_revision": revision.lower(),
        "built_image": target.name,
        "bytes": target.stat().st_size,
        "sha256": digest,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "ci_verified": False,
        "hardware_validated": False,
        "note": "Package authenticity only; Pi boot, radios, storage and UI remain hardware acceptance gates",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage a checksummed Redux Pi image for testing")
    parser.add_argument("--deploy", required=True, help="pi-gen deploy directory")
    parser.add_argument("--out", required=True, help="empty release folder")
    parser.add_argument("--revision", required=True, help="full 40-digit Git commit SHA")
    parser.add_argument("--image", help="select image filename if deploy contains multiple")
    args = parser.parse_args(argv)
    try:
        manifest = package_image(args.deploy, args.out, args.revision, args.image)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"redux package-image: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
