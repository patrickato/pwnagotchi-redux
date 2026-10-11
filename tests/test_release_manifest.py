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


def fixture_image(tmp_path, *, size=131072, signature=True, partitions=True,
                  ext4=True, label=True, overlap=False):
    raw = bytearray(size)
    if signature and size >= 512:
        raw[510:512] = b"\x55\xaa"
    if partitions and size >= 131072:
        for idx, (kind, start, count) in enumerate([
            (0x0c, 8, 32), (0x83, 40 if not overlap else 16, 80), (0x83, 120, 128)
        ]):
            off = 446 + idx * 16
            raw[off + 4] = kind
            raw[off + 8:off + 12] = start.to_bytes(4, "little")
            raw[off + 12:off + 16] = count.to_bytes(4, "little")
        if ext4:
            raw[120 * 512 + 1024 + 0x38:120 * 512 + 1024 + 0x3a] = b"\x53\xef"
        if label:
            raw[120 * 512 + 1024 + 0x78:120 * 512 + 1024 + 0x80] = b"REDUXCAP"
        # Only signature evidence: fixture is not mountable/bootable.
        boot = 8 * 512
        raw[boot + 82:boot + 87] = b"FAT32"
        raw[boot + 510:boot + 512] = b"\x55\xaa"
        root = 40 * 512 + 1024
        raw[root + 0x38:root + 0x3a] = b"\x53\xef"
    path = tmp_path / "redux-synthetic.img.xz"
    path.write_bytes(lzma.compress(bytes(raw), format=lzma.FORMAT_XZ))
    return path


def test_release_manifest_matches_verified_artifact(tmp_path):
    image = fixture_image(tmp_path)
    record = publish(image, REV, PI_GEN_REV, NEXMON_REV, minimum_bytes=512)
    assert record["disk_bytes"] == 131072
    assert record["captures_label"] == "REDUXCAP"
    assert len(record["partitions"]) == 3
    assert [p["format"] for p in record["partition_filesystems"]] == ["fat32", "ext4", "ext4"]
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
        inspect_image(image, minimum_bytes=256 * 1024)
    with pytest.raises(ValueError, match="revision"):
        publish(image, "not-a-revision", PI_GEN_REV, NEXMON_REV, minimum_bytes=512)
    assert not list(tmp_path.glob("*.release.json"))


def test_reject_missing_third_capture_partition(tmp_path):
    image = fixture_image(tmp_path, partitions=False)
    with pytest.raises(ValueError, match="partition"):
        inspect_image(image, minimum_bytes=512)


def test_reject_corrupt_ext4_and_wrong_label(tmp_path):
    image = fixture_image(tmp_path, ext4=False)
    with pytest.raises(ValueError, match="ext4 filesystem signature"):
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


def test_reject_missing_boot_fat32_or_root_ext4(tmp_path):
    raw = bytearray(lzma.decompress(fixture_image(tmp_path).read_bytes()))
    for offset, message in ((8 * 512 + 82, "fat32"), (40 * 512 + 1024 + 0x38, "ext4")):
        tampered = bytearray(raw)
        tampered[offset:offset + 2] = bytes(2)
        image = tmp_path / "tampered.img.xz"
        image.write_bytes(lzma.compress(bytes(tampered)))
        with pytest.raises(ValueError, match=message):
            inspect_image(image, minimum_bytes=512)


def test_reject_extra_mbr_partition(tmp_path):
    image = fixture_image(tmp_path)
    raw = bytearray(lzma.decompress(image.read_bytes()))
    raw[494 + 4] = 0x83
    image.write_bytes(lzma.compress(bytes(raw)))
    with pytest.raises(ValueError, match="fourth MBR"):
        inspect_image(image, minimum_bytes=512)
