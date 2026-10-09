"""Small synthetic disk files exercise the real release verifier without pi-gen."""
import hashlib
import json
import lzma
from pathlib import Path

import pytest
import importlib.util

# Image-build helpers are deliberately standalone scripts, outside the installed
# redux package; load by file path just as the real build.sh does.
SOURCE = Path(__file__).resolve().parents[1] / "image" / "release_manifest.py"
SPEC = importlib.util.spec_from_file_location("redux_release_manifest", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
inspect_image = MODULE.inspect_image
publish = MODULE.publish

REV = "a" * 40
PI_GEN_REV = "b" * 40
NEXMON_REV = "c" * 40


def fixture_image(tmp_path, *, size=4096, signature=True, partitions=True,
                  ext4=True, label=True, overlap=False):
    raw = bytearray(size)
    if signature and size >= 512:
        raw[510:512] = b"\x55\xaa"
    if partitions and size >= 4096:
        for idx, (kind, start, count) in enumerate([
            (0x0c, 1, 1), (0x83, 2 if not overlap else 1, 1), (0x83, 3, 5)
        ]):
            off = 446 + idx * 16
            raw[off + 4] = kind
            raw[off + 8:off + 12] = start.to_bytes(4, "little")
            raw[off + 12:off + 16] = count.to_bytes(4, "little")
        if ext4:
            raw[3 * 512 + 1024 + 0x38:3 * 512 + 1024 + 0x3a] = b"\x53\xef"
        if label:
            raw[3 * 512 + 1024 + 0x78:3 * 512 + 1024 + 0x80] = b"REDUXCAP"
    path = tmp_path / "redux-synthetic.img.xz"
    path.write_bytes(lzma.compress(bytes(raw), format=lzma.FORMAT_XZ))
    return path


def test_release_manifest_matches_verified_artifact(tmp_path):
    image = fixture_image(tmp_path)
    record = publish(image, REV, PI_GEN_REV, NEXMON_REV, minimum_bytes=512)
    assert record["disk_bytes"] == 4096
    assert record["captures_label"] == "REDUXCAP"
    assert len(record["partitions"]) == 3
    assert record["sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert record["revisions"] == {
        "redux": REV, "pi_gen": PI_GEN_REV, "nexmon": NEXMON_REV
    }
    assert record["hardware_tested"] is False
    assert record["boot_tested"] is False
    manifest = image.with_name(image.name + ".release.json")
    checksum = image.with_name(image.name + ".sha256")
    assert json.loads(manifest.read_text()) == record
    assert checksum.read_text() == f"{record['sha256']}  {image.name}\n"


def test_reject_truncated_and_corrupted_release_image(tmp_path):
    image = fixture_image(tmp_path)
    image.write_bytes(image.read_bytes()[:20])
    with pytest.raises(ValueError, match="corrupt|truncated"):
        inspect_image(image, minimum_bytes=512)


def test_reject_invalid_disk_boot_sector(tmp_path):
    image = fixture_image(tmp_path, signature=False)
    with pytest.raises(ValueError, match="0x55AA"):
        inspect_image(image, minimum_bytes=512)


def test_reject_short_image_and_invalid_provenance(tmp_path):
    image = fixture_image(tmp_path)
    with pytest.raises(ValueError, match="too small"):
        inspect_image(image, minimum_bytes=8 * 1024)
    with pytest.raises(ValueError, match="revision"):
        publish(image, "not-a-revision", PI_GEN_REV, NEXMON_REV, minimum_bytes=512)
    assert not list(tmp_path.glob("*.release.json"))


def test_reject_missing_third_capture_partition(tmp_path):
    image = fixture_image(tmp_path, partitions=False)
    with pytest.raises(ValueError, match="partition"):
        inspect_image(image, minimum_bytes=512)


def test_reject_corrupt_ext4_and_wrong_label(tmp_path):
    image = fixture_image(tmp_path, ext4=False)
    with pytest.raises(ValueError, match="ext4 superblock"):
        inspect_image(image, minimum_bytes=512)
    image = fixture_image(tmp_path, label=False)
    with pytest.raises(ValueError, match="filesystem label"):
        inspect_image(image, minimum_bytes=512)


def test_reject_overlapping_partitions(tmp_path):
    image = fixture_image(tmp_path, overlap=True)
    with pytest.raises(ValueError, match="overlapping"):
        inspect_image(image, minimum_bytes=512)


def test_refuse_symlink_artifacts(tmp_path):
    image = fixture_image(tmp_path)
    link = tmp_path / "symlink.img.xz"
    link.symlink_to(image)
    with pytest.raises(ValueError, match="symlink"):
        inspect_image(link, minimum_bytes=512)
