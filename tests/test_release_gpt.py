"""Synthetic A/B GPT release verification; no mounts, root, network or Pi."""
import importlib.util
import lzma
from pathlib import Path
import struct
import sys
import zlib

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "image/release_manifest.py"
SPEC = importlib.util.spec_from_file_location("redux_gpt_release", SCRIPT)
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


def sample(tmp_path, bad=None):
    """Build a small but structurally faithful six-partition GPT fixture."""
    sectors = 512
    raw = bytearray(sectors * 512)
    raw[510:512] = b"\x55\xaa"
    raw[450] = 0xEE
    struct.pack_into("<II", raw, 454, 1, sectors - 1)
    starts = [40, 70, 100, 130, 200, 270]
    counts = [20, 20, 20, 50, 50, 150]
    entries = bytearray(128 * 128)
    for index, (name, start, count) in enumerate(
            zip(release._GPT_NAMES, starts, counts)):
        offset = index * 128
        entries[offset:offset + 16] = bytes([index + 1]) * 16
        entries[offset + 16:offset + 32] = bytes([index + 7]) * 16
        struct.pack_into("<QQ", entries, offset + 32, start, start + count - 1)
        encoded = name.encode("utf-16-le")
        entries[offset + 56:offset + 56 + len(encoded)] = encoded
    if bad == "extra_partition":
        entries[6 * 128:6 * 128 + 16] = b"\x11" * 16
    if bad == "wrong_name":
        entries[5 * 128 + 56:5 * 128 + 58] = "X".encode("utf-16-le")
    if bad == "overlap":
        struct.pack_into("<QQ", entries, 5 * 128 + 32, 210, 419)
    if bad == "outside_usable":
        struct.pack_into("<QQ", entries, 5 * 128 + 32, 270, 485)
    crc = zlib.crc32(entries)

    def header(this, other, entry_lba):
        sector = bytearray(512)
        sector[:8] = b"EFI PART"
        struct.pack_into("<IIII", sector, 8, 0x10000, 92, 0, 0)
        struct.pack_into("<QQQQ", sector, 24, this, other, 34, sectors - 34)
        sector[56:72] = b"A" * 16
        struct.pack_into("<QIII", sector, 72, entry_lba, 128, 128, crc)
        struct.pack_into("<I", sector, 16, zlib.crc32(sector[:92]))
        return sector

    raw[1024:1024 + len(entries)] = entries
    raw[512:1024] = header(1, sectors - 1, 2)
    raw[(sectors - 33) * 512:(sectors - 1) * 512] = entries
    raw[-512:] = header(sectors - 1, 1, sectors - 33)
    if bad == "primary_header_crc":
        raw[520] ^= 1
    if bad == "primary_entries_crc":
        raw[2048] ^= 1
    if bad == "backup_header_crc":
        raw[-490] ^= 1
    if bad == "backup_entries":
        raw[(sectors - 33) * 512] ^= 1
    if bad == "backup_missing":
        raw[-512:] = bytes(512)
    if bad == "backup_wrong_disk":
        raw[-512 + 56] ^= 1
    if bad == "truncated_primary":
        raw = raw[:1600]
    else:
        capture_offset = starts[-1] * 512 + 1024
        if bad != "missing_ext4":
            raw[capture_offset + 0x38:capture_offset + 0x3A] = b"\x53\xef"
        if bad != "wrong_label":
            raw[capture_offset + 0x78:capture_offset + 0x80] = b"REDUXCAP"

    target = tmp_path / "redux-ab.img.xz"
    target.write_bytes(lzma.compress(bytes(raw)))
    return target


def test_real_compressed_gpt_release_is_accepted(tmp_path):
    image = sample(tmp_path)
    result = release.inspect_image(image, minimum_bytes=512)
    assert result["partition_table"] == "gpt"
    assert result["captures_label"] == "REDUXCAP"
    assert len(result["partitions"]) == 6
    assert [p["gpt_name"] for p in result["partitions"]] == list(release._GPT_NAMES)
    record = release.publish(image, "a" * 40, "b" * 40, "c" * 40,
                             minimum_bytes=512)
    assert "disk_gpt_primary_backup" in record["checks"]
    assert record["boot_tested"] is False
    assert record["hardware_tested"] is False


@pytest.mark.parametrize("bad", [
    "extra_partition", "wrong_name", "overlap", "outside_usable",
    "primary_header_crc", "primary_entries_crc", "backup_header_crc",
    "backup_entries", "backup_missing", "backup_wrong_disk",
    "missing_ext4", "wrong_label", "truncated_primary",
])
def test_reject_unexpected_or_corrupted_ab_images(tmp_path, bad):
    with pytest.raises(ValueError):
        release.inspect_image(sample(tmp_path, bad), minimum_bytes=512)


def test_backup_header_must_be_at_the_end_of_the_disk(tmp_path):
    image = sample(tmp_path)
    raw = lzma.decompress(image.read_bytes()) + bytes(512)
    image.write_bytes(lzma.compress(raw))
    with pytest.raises(ValueError, match="disk length"):
        release.inspect_image(image, minimum_bytes=512)
