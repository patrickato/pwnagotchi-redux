"""OTA health and state ledger must reject redirected, unstable field records.

Host-only fixtures: no RAUC, firmware selector, Pi, service, or reboot access.
"""
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("redux_ota_test_module", REPO / "boot/ota.py")
ota = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ota
spec.loader.exec_module(ota)

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



@pytest.mark.parametrize("startup", [
    "fixture",
    "",
    None,
    True,
    "2026-10-17T20:00:00",
    "2999-01-01T00:00:00+00:00",
    "2026-10-17T20:00:00+00:00" * 10,
])
def test_ota_health_requires_real_timezone_aware_nonfuture_start(tmp_path, startup):
    boot_id = tmp_path / "boot-id"
    boot_id.write_text(BOOT_ID)
    health = tmp_path / "boot.json"
    altered = checkpoint()
    altered["started_utc"] = startup
    health.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="stale or incomplete"):
        ota.validate_boot_checkpoint(147, checkpoint=health, boot_id=boot_id)


def test_ota_health_does_not_accept_fake_kernel_boot_id(tmp_path):
    boot_id = tmp_path / "boot-id"
    boot_id.write_text("a-fake-boot-identity")
    health = tmp_path / "boot.json"
    altered = checkpoint()
    altered["boot_id"] = "a-fake-boot-identity"
    health.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="stale or incomplete"):
        ota.validate_boot_checkpoint(147, checkpoint=health, boot_id=boot_id)



def _ota_mount_health_fixture(tmp_path, monkeypatch):
    """Four independently identifiable mounted volumes on a synthetic disk."""
    points = {
        "/media/root-ro": ["ext4", "ro,nodev", "4"],
        "/boot/firmware": ["vfat", "ro,nodev", "2"],
        "/boot/redux-control": ["vfat", "ro,nodev", "1"],
        "/captures": ["ext4", "rw,nodev", "6"],
    }
    devices = {}
    for part in ("1", "2", "4", "6"):
        device = tmp_path / ("partition" + part)
        device.write_bytes(b"fixture")
        devices[part] = str(device)
    measured = {
        point: {"FSTYPE": kind, "OPTIONS": options, "SOURCE": devices[part]}
        for point, (kind, options, part) in points.items()
    }

    def command(arguments):
        if arguments == ["systemctl", "is-active", "redux.service"]:
            return "active"
        if arguments == ["systemctl", "show", "-p", "RuntimeWatchdogUSec", "--value"]:
            return "20000000"
        if arguments[:3] == ["findmnt", "-n", "-o"] and len(arguments) == 5:
            return measured[arguments[4]][arguments[3]]
        raise AssertionError(f"unexpected OTA probe: {arguments!r}")

    monkeypatch.setattr(ota, "command", command)
    monkeypatch.setattr(ota, "dt_integer", lambda name: 2 if name == "partition"
                        else pytest.fail(f"unexpected device-tree read {name}"))
    monkeypatch.setattr(ota, "validate_boot_checkpoint", lambda pid: checkpoint(pid))
    real_read_text = Path.read_text

    def watchdog_read(self, *args, **kwargs):
        if str(self) == "/sys/class/watchdog/watchdog0/state":
            return "active\n"
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", watchdog_read)
    return {"current": "A", "devices": devices}, measured


def test_ota_health_accepts_exactly_matched_boot_storage(tmp_path, monkeypatch):
    context, _ = _ota_mount_health_fixture(tmp_path, monkeypatch)
    assert ota.check_health(context, expected_pid=321) is None


@pytest.mark.parametrize("point,key,spoof", [
    ("/captures", "FSTYPE", "tmpfs"),
    ("/captures", "OPTIONS", "ro,nodev"),
    ("/captures", "SOURCE", "/nonexistent/other-capture-partition"),
    ("/media/root-ro", "FSTYPE", "vfat"),
    ("/media/root-ro", "OPTIONS", "rw,nodev"),
    ("/media/root-ro", "SOURCE", "/nonexistent/other-root-partition"),
    ("/boot/firmware", "FSTYPE", "ext4"),
    ("/boot/firmware", "OPTIONS", "rw,nodev"),
    ("/boot/firmware", "SOURCE", "/nonexistent/wrong-boot-partition"),
    ("/boot/redux-control", "FSTYPE", "ext4"),
    ("/boot/redux-control", "OPTIONS", "rw,nodev"),
    ("/boot/redux-control", "SOURCE", "/nonexistent/wrong-control-partition"),
])
def test_ota_health_refuses_substitute_storage(
        tmp_path, monkeypatch, point, key, spoof):
    context, measured = _ota_mount_health_fixture(tmp_path, monkeypatch)
    measured[point][key] = spoof
    with pytest.raises((OSError, ValueError)):
        ota.check_health(context, expected_pid=321)
