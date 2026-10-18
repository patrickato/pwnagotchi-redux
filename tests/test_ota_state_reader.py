"""OTA health and state ledger must reject redirected, unstable field records.

Host-only fixtures: no RAUC, firmware selector, Pi, service, or reboot access.
"""
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from boot import ota

BOOT_ID = "00000000-0000-0000-0000-000000000001"


def checkpoint(pid=147):
    return {
        "schema": 1,
        "pid": pid,
        "boot_id": BOOT_ID,
        "started_utc": "2026-10-17T20:00:00+00:00",
        "clean_shutdown": False,
    }


def test_ota_factory_ledger_default_and_pinned_valid_records(tmp_path):
    ledger = tmp_path / "state.json"
    assert ota.load_state(ledger, "A") == ota.initial_state("A")
    state = ota.initial_state("A")
    state["pending"] = "B"
    ledger.write_text(json.dumps(state))
    assert ota.load_state(ledger, "A") == state

    boot_id = tmp_path / "boot-id"
    boot_id.write_text(BOOT_ID + "\n")
    health = tmp_path / "boot.json"
    health.write_text(json.dumps(checkpoint()))
    assert ota.validate_boot_checkpoint(147, checkpoint=health, boot_id=boot_id) == checkpoint()
    with pytest.raises(ValueError, match="stale or incomplete"):
        ota.validate_boot_checkpoint(148, checkpoint=health, boot_id=boot_id)


@pytest.mark.parametrize("kind", ["symlink", "dangling", "hardlink", "oversized", "directory"])
def test_ota_ledger_refuses_unsafe_field_state(tmp_path, kind):
    ledger = tmp_path / "state.json"
    private = tmp_path / "private"
    private.write_text(json.dumps(ota.initial_state("A")))
    if kind == "symlink":
        ledger.symlink_to(private)
    elif kind == "dangling":
        ledger.symlink_to(tmp_path / "missing-target")
    elif kind == "hardlink":
        os.link(private, ledger)
    elif kind == "oversized":
        ledger.write_bytes(b" " * 16385)
    else:
        ledger.mkdir()

    with pytest.raises((OSError, ValueError)):
        ota.load_state(ledger, "A")
    assert private.read_text() == json.dumps(ota.initial_state("A"))


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "oversized", "directory"])
def test_ota_health_checkpoint_refuses_unsafe_record(tmp_path, kind):
    checkpoint_path = tmp_path / "boot.json"
    boot_id = tmp_path / "boot-id"
    boot_id.write_text(BOOT_ID)
    private = tmp_path / "private"
    private.write_text(json.dumps(checkpoint()))
    if kind == "symlink":
        checkpoint_path.symlink_to(private)
    elif kind == "hardlink":
        os.link(private, checkpoint_path)
    elif kind == "oversized":
        checkpoint_path.write_bytes(b"x" * 65537)
    else:
        checkpoint_path.mkdir()

    with pytest.raises((OSError, ValueError)):
        ota.validate_boot_checkpoint(147, checkpoint=checkpoint_path, boot_id=boot_id)
    assert private.read_text() == json.dumps(checkpoint())


def test_ota_ledger_rejects_symlinked_parent_without_loading_state(tmp_path):
    private = tmp_path / "private-dir"
    private.mkdir()
    (private / "state.json").write_text(json.dumps(ota.initial_state("A")))
    redirected = tmp_path / "rauc"
    redirected.symlink_to(private, target_is_directory=True)
    with pytest.raises(ValueError, match="directory"):
        ota.load_state(redirected / "state.json", "A")


def test_ota_checkpoint_detects_in_place_change_during_inspection(tmp_path, monkeypatch):
    path = tmp_path / "boot.json"
    path.write_text(json.dumps(checkpoint()))
    previous = ota.os.fstat
    inode = path.stat().st_ino
    hits = [0]

    def changed_stat(fd):
        value = previous(fd)
        if value.st_ino == inode and stat.S_ISREG(value.st_mode):
            hits[0] += 1
            if hits[0] == 2:
                return SimpleNamespace(
                    st_mode=value.st_mode, st_nlink=value.st_nlink,
                    st_size=value.st_size, st_dev=value.st_dev,
                    st_ino=value.st_ino, st_mtime_ns=value.st_mtime_ns + 1,
                )
        return value

    with monkeypatch.context() as patch:
        patch.setattr(ota.os, "fstat", changed_stat)
        with pytest.raises(ValueError, match="changed during inspection"):
            ota._read_bounded_state_json(path, limit=65536)
    assert hits[0] == 2
