"""Build artifact acceptance uses synthetic files; never flashes or mounts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "redux_field_image_qualification",
    ROOT / "scripts/qualify-field-image.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

REV = "a" * 40


def fixture(tmp_path):
    workspace = tmp_path / "image-build"
    deploy = workspace / "pi-gen/deploy"
    deploy.mkdir(parents=True)
    stage = workspace / "pi-gen/stage-redux/00-redux/files"
    stage.mkdir(parents=True)
    (stage / "regulatory-country").write_text("US\n")
    common = workspace / "pi-gen/scripts/common"
    common.parent.mkdir(parents=True)
    common.write_text(
        "bootstrap(){\n"
        "\tBOOTSTRAP_ARGS+=(--arch arm64)\n"
        "\tBOOTSTRAP_ARGS+=(--force-check-gpg)\n"
        "\tBOOTSTRAP_ARGS+=(--include gnupg)\n"
        "}\n"
    )
    (stage / "redux-revision").write_text(REV + "\n")
    live = stage / "pipeline/live.toml"
    live.parent.mkdir()
    live.write_text((ROOT / "config/live.example.toml").read_text())
    script = workspace / "pi-gen/export-image/05-finalise/01-run.sh"
    script.parent.mkdir(parents=True)
    script.write_text(
        'python3 "${STAGE_DIR}/redux-root-payload.py" --profile lite '
        '--root "${ROOTFS_DIR}" --expected-country US\n')
    image = deploy / "redux-field-fixture.img.xz"
    # Qualification intentionally does NOT redo the producer's xz stream
    # verification; tests use synthetic compressed bytes to prove binding.
    image.write_bytes(b"synthetic image data")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    info = {
        "schema": 1, "image": image.name,
        "revisions": {"redux": REV},
        "image_profile": "lite",
        "partition_table": "mbr",
        "profile_match_verified": True,
        "captures_label": "REDUXCAP",
        "captures_filesystem": "ext4",
        "checks": ["xz_stream", "disk_mbr_partitions",
                   "partition_filesystem_signatures", "sha256"],
        "hardware_tested": False,
        "boot_tested": False,
        "sha256": digest,
        "compressed_bytes": image.stat().st_size,
    }
    (deploy / (image.name + ".release.json")).write_text(json.dumps(info))
    (deploy / (image.name + ".sha256")).write_text(f"{digest}  {image.name}\n")
    return workspace, image, script, stage


def accepted(workspace):
    return gate.qualify(workspace, revision=REV, country="US", profile="lite")


def test_fresh_field_image_qualifies_without_false_physical_claim(tmp_path):
    workspace, *_ = fixture(tmp_path)
    report = accepted(workspace)
    assert report["regulatory_country"] == "US"
    assert report["source_revision"] == REV
    assert report["artifact_integrity_verified"] is True
    assert report["checks"]["signed_debian_bootstrap_policy"] is True
    assert report["physical_boot_verified"] is False
    assert report["radio_capture_verified"] is False
    assert all(report["checks"].values())


def test_old_unsigned_bootstrap_cannot_pass_artifact_qualification(tmp_path):
    workspace, *_ = fixture(tmp_path)
    script = workspace / "pi-gen/scripts/common"
    script.write_text(script.read_text().replace(
        "\tBOOTSTRAP_ARGS+=(--force-check-gpg)\n", ""
    ))
    with pytest.raises(ValueError, match="signed_debian_bootstrap_policy"):
        accepted(workspace)
    script.write_text(script.read_text() + "# --no-check-gpg\n")
    with pytest.raises(ValueError, match="signed_debian_bootstrap_policy"):
        accepted(workspace)


def test_unknown_debootstrap_bootstrap_source_cannot_pass(tmp_path):
    workspace, *_ = fixture(tmp_path)
    common = workspace / "pi-gen/scripts/common"
    common.write_text("bootstrap() { echo skipped; }\n")
    with pytest.raises(ValueError, match="signed_debian_bootstrap_policy"):
        accepted(workspace)


def test_tampered_image_or_release_checksum_fails_before_publish(tmp_path):
    workspace, image, *_ = fixture(tmp_path)
    image.write_bytes(image.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="compressed_sha256"):
        accepted(workspace)
    workspace, image, *_ = fixture(tmp_path / "fresh")
    checksum = image.with_name(image.name + ".sha256")
    checksum.write_text("f" * 64 + "  wrong.img.xz\n")
    with pytest.raises(ValueError, match="checksum_companion"):
        accepted(workspace)


def test_missing_or_wrong_regulatory_export_proof_fails(tmp_path):
    workspace, image, script, stage = fixture(tmp_path)
    script.write_text(script.read_text().replace(
        "--expected-country US", "--expected-country CA"))
    with pytest.raises(ValueError, match="mounted_root_country_gate"):
        accepted(workspace)
    script.write_text('echo no-mounted-root-check\n')
    with pytest.raises(ValueError, match="mounted_root_country_gate"):
        accepted(workspace)
    with pytest.raises(ValueError, match="selected_country"):
        gate.qualify(workspace, revision=REV, country="CA")
    script.write_text(
        'python3 "${STAGE_DIR}/redux-root-payload.py" --profile lite '
        '--root "${ROOTFS_DIR}" --expected-country US\n')
    (stage / "regulatory-country").unlink()
    with pytest.raises(ValueError, match="missing qualification input"):
        accepted(workspace)


def test_stale_revision_or_dirty_marker_rejected(tmp_path):
    workspace, image, _, stage = fixture(tmp_path)
    with pytest.raises(ValueError, match="source_revision"):
        gate.qualify(workspace, revision="b" * 40, country="US")
    (stage / "redux-revision").write_text(REV + "\ndirty working tree\n")
    with pytest.raises(ValueError, match="baked_revision"):
        accepted(workspace)
    (stage / "redux-revision").write_text(REV + "\n")
    manifest = image.with_name(image.name + ".release.json")
    record = json.loads(manifest.read_text())
    record["revisions"]["redux"] = "b" * 40
    manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="source_revision"):
        accepted(workspace)


def test_missing_duplicate_symlink_and_wrong_profile_fail(tmp_path):
    workspace, image, *_ = fixture(tmp_path)
    duplicate = image.with_name("other.img.xz")
    duplicate.write_bytes(b"x")
    with pytest.raises(ValueError, match="exactly one"):
        accepted(workspace)
    duplicate.unlink()
    backing = image.with_name("backing-file.bin")
    image.rename(backing)
    image.symlink_to(backing)
    with pytest.raises(ValueError, match="unsafe"):
        accepted(workspace)
    image.unlink()
    backing.rename(image)
    with pytest.raises(ValueError, match="missing qualification input"):
        gate.qualify(workspace, revision=REV, country="US", profile="ab")
    # Supply the A/B clone gate so the profile mismatch, not the absent
    # clone script, becomes the next independent rejection condition.
    clone = workspace / "pi-gen/export-image/redux-ota-clone.sh"
    clone.write_text(
        'python3 "${STAGE_DIR}/redux-root-payload.py" --profile ab '
        '--root "$ROOTFS_DIR" --other-root "$other" --expected-country US\n')
    with pytest.raises(ValueError, match="partition_profile"):
        gate.qualify(workspace, revision=REV, country="US", profile="ab")


def test_field_helper_qualifies_before_latest_symlink():
    script = (ROOT / "scripts/build-field-image.sh").read_text()
    assert "scripts/qualify-field-image.py" in script
    assert script.index("scripts/qualify-field-image.py") < script.index('mv -Tf -- "$pending" "$latest"')
    assert script.index('latest="$HOME/redux-field-latest"') < script.index('ssh-keygen -q')
    assert '[[ ! -e "$latest" || -L "$latest" ]]' in script
    assert 'ln -s -- "$workspace" "$pending"' in script
    assert 'trap \'rm -f -- "$pending"\' EXIT' in script
    assert "git status --porcelain" in script
    assert "--revision \"$revision\" --country \"$country\"" in script


def test_occupied_latest_path_aborts_before_key_generation_or_build(tmp_path):
    import os
    import subprocess
    home = tmp_path / "home"
    home.mkdir()
    reserved = home / "redux-field-latest"
    reserved.mkdir()
    env = dict(os.environ, HOME=str(home))
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/build-field-image.sh")],
        capture_output=True, text=True, env=env, timeout=5)
    assert result.returncode != 0
    assert "exists and is not a symlink" in result.stderr
    assert not (home / ".ssh").exists()
    assert reserved.is_dir()
