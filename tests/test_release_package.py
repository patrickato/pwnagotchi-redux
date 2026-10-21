"""Provenance packaging tests; synthetic bytes are not presented as bootable images."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("redux_package_image", ROOT / "scripts/package_image.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
REV = "c" * 40


def test_copy_and_checksum_manifest(tmp_path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    sample = deploy / "redux-test.img.xz"
    sample.write_bytes(b"synthetic-image-fixture-not-flashable")
    out = tmp_path / "releases"
    record = package.package_image(deploy, out, REV)
    digest = hashlib.sha256(sample.read_bytes()).hexdigest()
    assert (out / sample.name).read_bytes() == sample.read_bytes()
    assert (out / (sample.name + ".sha256")).read_text() == f"{digest}  {sample.name}\n"
    assert json.loads((out / "manifest.json").read_text()) == record
    assert record["source_revision"] == REV and record["sha256"] == digest
    assert record["hardware_validated"] is False
    assert record["ci_verified"] is False


def test_missing_ambiguous_and_selected_image(tmp_path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    with pytest.raises(ValueError, match="expected one"):
        package.pick_image(deploy)
    (deploy / "one.img").write_bytes(b"image1")
    (deploy / "two.img.xz").write_bytes(b"image2")
    with pytest.raises(ValueError, match="expected one"):
        package.pick_image(deploy)
    assert package.pick_image(deploy, "two.img.xz").name == "two.img.xz"
    with pytest.raises(ValueError, match="regular file"):
        package.pick_image(deploy, "../outside.img")


def test_no_replacing_existing_release(tmp_path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "redux.img").write_bytes(b"synthetic image bytes")
    out = tmp_path / "out"
    package.package_image(deploy, out, REV)
    with pytest.raises(FileExistsError, match="already has"):
        package.package_image(deploy, out, REV)


def test_reject_image_symlinks_and_zero_bytes(tmp_path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    outside = tmp_path / "external.img"
    outside.write_bytes(b"external")
    (deploy / "link.img").symlink_to(outside)
    assert package.image_candidates(deploy) == []
    (deploy / "empty.img.xz").touch()
    with pytest.raises(ValueError, match="empty"):
        package.package_image(deploy, tmp_path / "output", REV)


def test_reject_bad_revision_and_output_dir(tmp_path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "data.img").write_bytes(b"fixture")
    with pytest.raises(ValueError, match="revision"):
        package.package_image(deploy, tmp_path / "out", "main")
    with pytest.raises(ValueError, match="must differ"):
        package.package_image(deploy, deploy, REV)
    external = tmp_path / "actual"
    external.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(external)
    with pytest.raises(ValueError, match="symbolic"):
        package.package_image(deploy, link, REV)
