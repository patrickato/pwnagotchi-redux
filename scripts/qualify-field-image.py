#!/usr/bin/env python3
"""Qualify ONE already-exported disposable Redux field image before presenting it.

Read-only post-build evidence. The producer's release_manifest.py already
stream-verified xz, partition topology and filesystem signatures. This step
re-hashes the final downloadable compressed bytes and binds its manifest to
the exact source revision, selected field regulatory country, export-gate
command and staged immutable configuration. No mounts, sudo, radios or Pi.
This is NOT physical boot or capture acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import tomllib

_SHA = re.compile(r"^[0-9a-f]{40}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


def regular(path: Path, *, limit=None) -> Path:
    try:
        st = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"missing qualification input: {path.name}") from exc
    if not stat.S_ISREG(st.st_mode) or (limit is not None and st.st_size > limit):
        raise ValueError(f"unsafe or oversized qualification input: {path.name}")
    return path


def country_code(code):
    if (not isinstance(code, str) or not re.fullmatch(r"[A-Z]{2}", code)
            or code in ("AA", "ZZ")):
        raise ValueError("invalid operator regulatory country")
    return code


def qualify(workspace: Path, *, revision: str, country: str, profile="lite"):
    if not isinstance(revision, str) or not _SHA.fullmatch(revision):
        raise ValueError("expected source revision must be a full Git SHA")
    country = country_code(country)
    if profile not in ("lite", "ab"):
        raise ValueError("expected profile must be lite or ab")
    workspace = Path(workspace)
    pi_gen = workspace / "pi-gen"
    deploy = pi_gen / "deploy"
    if not deploy.is_dir() or deploy.is_symlink():
        raise ValueError("missing real image deploy directory")
    images = sorted(deploy.glob("*.img.xz"))
    if len(images) != 1:
        raise ValueError(f"expected exactly one compressed image; found {len(images)}")
    image = regular(images[0])
    manifest = regular(image.with_name(image.name + ".release.json"), limit=64*1024)
    checksum = regular(image.with_name(image.name + ".sha256"), limit=256)
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid release manifest") from exc
    if not isinstance(record, dict):
        raise ValueError("release manifest must be an object")
    revisions = record.get("revisions")
    if not isinstance(revisions, dict):
        raise ValueError("release manifest has invalid revisions")
    checks = {
        "schema": record.get("schema") == 1,
        "artifact_name": record.get("image") == image.name,
        "source_revision": revisions.get("redux") == revision,
        "partition_profile": (
            record.get("image_profile") == profile
            and record.get("profile_match_verified") is True
            and record.get("partition_table") == ("mbr" if profile == "lite" else "gpt")
        ),
        "capture_partition": (
            record.get("captures_label") == "REDUXCAP"
            and record.get("captures_filesystem") == "ext4"
        ),
        "export_signature_checks": (
            isinstance(record.get("checks"), list)
            and "xz_stream" in record["checks"]
            and "partition_filesystem_signatures" in record["checks"]
            and "sha256" in record["checks"]
        ),
        "no_false_hardware_claims": (
            record.get("hardware_tested") is False
            and record.get("boot_tested") is False
        ),
    }
    source = regular(pi_gen / "stage-redux/00-redux/files/redux-revision", limit=256)
    checks["baked_revision"] = source.read_text(encoding="ascii").strip() == revision
    selected = regular(pi_gen / "stage-redux/00-redux/files/regulatory-country", limit=8)
    checks["selected_country"] = selected.read_text(encoding="ascii").strip() == country
    config = regular(pi_gen / "stage-redux/00-redux/files/pipeline/live.toml",
                     limit=16*1024)
    live = tomllib.loads(config.read_text(encoding="utf-8")).get("live", {})
    if not isinstance(live, dict):
        raise ValueError("staged live configuration has no [live] table")
    checks["fail_closed_live_config"] = (
        live.get("require_regulatory_country") is True
        and live.get("regulatory_country_file") == "/etc/redux/regulatory-country"
        and live.get("allow_connected_capture") is False
    )
    # This runs within pi-gen before mounted partitions are unmounted:
    # the actual root-payload.py compares each installed country file.
    final = regular(pi_gen / "export-image/05-finalise/01-run.sh", limit=128*1024)
    script = final.read_text(encoding="utf-8")
    if profile == "ab":
        final = regular(pi_gen / "export-image/redux-ota-clone.sh", limit=128*1024)
        script = final.read_text(encoding="utf-8")
    checks["mounted_root_country_gate"] = (
        f"--expected-country {country}" in script
        and "redux-root-payload.py" in script
        and "--profile " + profile in script
    )
    hash_value = record.get("sha256")
    if not isinstance(hash_value, str) or not _HASH.fullmatch(hash_value):
        raise ValueError("missing/invalid compressed-image digest in release manifest")
    expected_checksum = f"{hash_value}  {image.name}\n"
    checks["checksum_companion"] = checksum.read_text(encoding="ascii") == expected_checksum
    h = hashlib.sha256()
    with image.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            h.update(chunk)
    checks["compressed_sha256"] = h.hexdigest() == hash_value
    checks["compressed_size"] = record.get("compressed_bytes") == image.stat().st_size
    failed = [key for key, ok in checks.items() if not ok]
    if failed:
        raise ValueError("field image qualification failed: " + ", ".join(failed))
    return {
        "schema": 1,
        "source_revision": revision,
        "image": image.name,
        "sha256": hash_value,
        "regulatory_country": country,
        "profile": profile,
        "checks": checks,
        "artifact_integrity_verified": True,
        "mounted_root_export_gate_evidence": True,
        "physical_boot_verified": False,
        "radio_capture_verified": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--profile", choices=("lite", "ab"), default="lite")
    args = parser.parse_args(argv)
    try:
        record = qualify(args.workspace, revision=args.revision,
                         country=args.country, profile=args.profile)
    except (ValueError, OSError, UnicodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(record, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
