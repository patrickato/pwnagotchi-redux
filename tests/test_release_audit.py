"""Transferred release audit and flash handoff: synthetic image, no disks."""
import hashlib
import importlib.util
import json
import lzma
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "image"))
import verify_release as audit

test_file = ROOT / "tests/test_release_manifest.py"
spec = importlib.util.spec_from_file_location("audit_fixture_release", test_file)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def published(tmp_path):
    image = fixture.fixture_image(tmp_path)
    record = fixture.publish(image, fixture.REV, fixture.PI_GEN_REV,
                             fixture.NEXMON_REV,
                             minimum_bytes=512, expected_profile="lite")
    return image, record


def audit_image(image, **kw):
    return audit.verify(image, minimum_bytes=512, **kw)


def test_verified_downloaded_triplet_and_separate_trusted_digest(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    image, record = published(build_dir)
    copy = tmp_path / "download"
    copy.mkdir()
    for name in (image.name, image.name + ".sha256",
                 image.name + ".release.json"):
        shutil.copy2(build_dir / name, copy / name)
    result = audit_image(copy / image.name, expected_profile="lite",
                         trusted_sha256=record["sha256"])
    assert result["manifest_verified"] is True
    assert result["checksum_verified"] is True
    assert result["partition_layout_verified"] is True
    assert result["trusted_digest_matched"] is True
    assert result["image_signature_verified"] is False
    assert result["hardware_boot_verified"] is False


def test_without_external_trusted_digest_provenance_not_verified(tmp_path):
    image, _ = published(tmp_path)
    result = audit_image(image)
    assert result["trusted_digest_matched"] is False
    assert result["image_signature_verified"] is False


@pytest.mark.parametrize("missing", ["release.json", "sha256"])
def test_missing_companion_is_refused(tmp_path, missing):
    image, _ = published(tmp_path)
    (tmp_path / (image.name + "." + missing)).unlink()
    with pytest.raises(OSError):
        audit_image(image)


@pytest.mark.parametrize("field,value", [
    ("image", "wrong.img.xz"),
    ("schema", 2),
    ("profile_match_verified", False),
    ("image_profile", "ab"),
    ("partition_table", "gpt"),
    ("compressed_bytes", 9),
    ("disk_bytes", 9),
    ("partitions", []),
    ("partition_filesystems", []),
    ("sha256", "0"*64),
    ("hardware_tested", True),
    ("boot_tested", True),
    ("checks", ["sha256"]),
    ("revisions", {}),
])
def test_tampered_manifest_is_rejected(tmp_path, field, value):
    image, _ = published(tmp_path)
    path = tmp_path / (image.name + ".release.json")
    record = json.loads(path.read_text())
    record[field] = value
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        audit_image(image)


def test_audit_rejects_checksum_mismatch_and_trusted_digest_mismatch(tmp_path):
    image, _ = published(tmp_path)
    with pytest.raises(ValueError, match="trusted release SHA256"):
        audit_image(image, trusted_sha256="0"*64)
    path = tmp_path / (image.name + ".sha256")
    path.write_text("0"*64 + "  " + image.name + "\n")
    with pytest.raises(ValueError, match="checksum sidecar"):
        audit_image(image)


def test_audit_rejects_replaced_image_with_stale_release_proof(tmp_path):
    image, _ = published(tmp_path)
    image.write_bytes(lzma.compress(bytes(4096)))
    with pytest.raises(ValueError):
        audit_image(image)


def test_audit_rejects_wrong_expected_profile(tmp_path):
    image, _ = published(tmp_path)
    with pytest.raises(ValueError, match="profile"):
        audit_image(image, expected_profile="ab")


@pytest.mark.parametrize("component", ["image", "release.json", "sha256"])
def test_audit_refuses_symlinked_artifact_or_sidecar(tmp_path, component):
    image, _ = published(tmp_path)
    target = image if component == "image" else tmp_path / (image.name + "." + component)
    saved = tmp_path / "untouched"
    saved.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(saved)
    with pytest.raises(ValueError, match="not a symlink"):
        audit_image(image)
    assert saved.is_file()


def test_duplicate_manifest_field_rejected(tmp_path):
    image, _ = published(tmp_path)
    path = tmp_path / (image.name + ".release.json")
    old = path.read_text()
    path.write_text(old.replace('"schema": 1,', '"schema": 1, "schema": 1,', 1))
    with pytest.raises(ValueError, match="duplicate"):
        audit_image(image)


def test_unbound_manifest_is_ineligible_for_handoff(tmp_path):
    image = fixture.fixture_image(tmp_path)
    fixture.publish(image, fixture.REV, fixture.PI_GEN_REV,
                    fixture.NEXMON_REV, minimum_bytes=512)
    with pytest.raises(ValueError, match="build-profile"):
        audit_image(image)


def test_cli_audit_is_readonly_and_rejects_unmatched_trusted_sha(tmp_path):
    image, record = published(tmp_path)
    args = [sys.executable, str(ROOT / "image/verify_release.py"),
            str(image), "--minimum-bytes", "512", "--expected-profile", "lite",
            "--trusted-sha256", record["sha256"]]
    result = subprocess.run(args, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["trusted_digest_matched"] is True
    fail = subprocess.run(args[:-1] + ["f"*64], text=True,
                          capture_output=True, timeout=15)
    assert fail.returncode != 0


def test_flash_refuses_missing_manifest_before_any_target_query(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("safe_flash", ROOT / "image/flash.py")
    flash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(flash)
    raw = bytearray(4096)
    raw[510:512] = b"\x55\xaa"
    image = tmp_path / "unchecked.img.xz"
    image.write_bytes(lzma.compress(raw))
    invoked = []
    def forbidden(*args):
        invoked.append(args)
        raise AssertionError("disk must not be queried before release audit")
    monkeypatch.setattr(flash, "disk_snapshot", forbidden)
    monkeypatch.setattr(sys, "argv", ["flash", str(image), "--sha256",
                                    hashlib.sha256(image.read_bytes()).hexdigest(),
                                    "--device", "/dev/fake"])
    with pytest.raises(SystemExit) as exc:
        flash.main()
    assert exc.value.code == 1
    assert invoked == []


def test_flash_auditor_success_and_explicit_checksum_only_opt_out(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("safe_flash", ROOT / "image/flash.py")
    flash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(flash)
    image, record = published(tmp_path)
    result = flash.validate_published_handoff(image, record["sha256"], "lite",
                                              minimum_bytes=512)
    assert result["manifest_verified"] is True
    assert "--checksum-only" in (ROOT / "image/flash.py").read_text()
