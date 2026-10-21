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
    state, _ = ota.transition(ota.initial_state("A"), "A", "A",
                              "set-primary", "B")
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
    measured["/"] = {"FSTYPE": "overlay"}

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



@pytest.mark.parametrize("bad", [
    {"pending": None, "states": {"A": "bad", "B": "good"}},
    {"pending": "B", "states": {"A": "good", "B": "bad"}},
    {"pending": "A", "states": {"A": "bad", "B": "good"}},
])
def test_ota_ledger_rejects_bad_committed_fallback_or_pending_candidate(tmp_path, bad):
    ledger = tmp_path / "state.json"
    record = ota.initial_state("A")
    record.update(bad)
    ledger.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="invalid OTA state"):
        ota.load_state(ledger, "A")
    # A contradictory record cannot be silently repaired by a health check.
    assert json.loads(ledger.read_text()) == record


def test_ota_ledger_retains_interrupted_selector_commit_recovery(tmp_path):
    ledger = tmp_path / "state.json"
    staged, _ = ota.transition(ota.initial_state("A"), "A", "A",
                               "set-primary", "B")
    ledger.write_text(json.dumps(staged))
    # The selector may already be B while the state write was interrupted:
    # pending==stable is deliberately valid for the recovery branch.
    loaded = ota.load_state(ledger, "B")
    assert loaded["pending"] == "B" and loaded["states"]["B"] == "good"
    recovered, committed = ota.transition(loaded, "B", "B",
                                           "set-state", "B", "good")
    assert committed is None
    assert recovered["pending"] is None
    assert "interrupted" in recovered["reason"]


def test_ota_ledger_refuses_unknown_stable_slot(tmp_path):
    ledger = tmp_path / "state.json"
    with pytest.raises(ValueError, match="unknown stable"):
        ota.load_state(ledger, "C")



def _selector_fixture(tmp_path, monkeypatch):
    selector = tmp_path / "autoboot.txt"
    selector.write_text(ota.autoboot("A"))
    commands = []
    monkeypatch.setattr(ota, "CONTROL", selector)
    monkeypatch.setattr(ota, "command", lambda args: commands.append(args) or "")
    return selector, commands


def test_ota_selector_commit_does_not_reuse_or_follow_fixed_staging_name(
        tmp_path, monkeypatch):
    selector, commands = _selector_fixture(tmp_path, monkeypatch)
    outside = tmp_path / "private"
    outside.write_text("leave this alone")
    abandoned = selector.with_suffix(".new")
    abandoned.symlink_to(outside)
    ota.commit_selector("B")
    assert selector.read_text() == ota.autoboot("B")
    assert outside.read_text() == "leave this alone"
    assert abandoned.is_symlink()
    assert not list(tmp_path.glob(".redux-autoboot-*"))
    assert commands == [
        ["mount", "-o", "remount,rw", "/boot/redux-control"],
        ["mount", "-o", "remount,ro", "/boot/redux-control"],
    ]


@pytest.mark.parametrize("stage", ["replace", "sync"])
def test_ota_selector_commit_failure_preserves_old_selector_and_cleans_stage(
        tmp_path, monkeypatch, stage):
    selector, commands = _selector_fixture(tmp_path, monkeypatch)
    with monkeypatch.context() as patcher:
        if stage == "replace":
            def fail_replace(source, target):
                assert Path(source).name.startswith(".redux-autoboot-")
                assert Path(target) == selector
                raise OSError("simulated selector rename interruption")
            patcher.setattr(ota.os, "replace", fail_replace)
        else:
            def fail_sync(_descriptor):
                raise OSError("simulated selector file sync interruption")
            patcher.setattr(ota.os, "fsync", fail_sync)
        with pytest.raises(OSError, match="simulated selector"):
            ota.commit_selector("B")
    assert selector.read_text() == ota.autoboot("A")
    assert not list(tmp_path.glob(".redux-autoboot-*"))
    assert commands[-1] == ["mount", "-o", "remount,ro", "/boot/redux-control"]


def test_ota_selector_commit_refuses_symlinked_selector_before_remount(
        tmp_path, monkeypatch):
    selector, commands = _selector_fixture(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.write_text("protected")
    selector.unlink()
    selector.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        ota.commit_selector("B")
    assert outside.read_text() == "protected"
    assert commands == []


def test_ota_selector_rejects_invalid_slot_without_remount(
        tmp_path, monkeypatch):
    selector, commands = _selector_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="invalid stable slot"):
        ota.commit_selector("C")
    assert selector.read_text() == ota.autoboot("A")
    assert commands == []


def _valid_runtime_partition_context():
    devices = {
        str(number): ("/dev/disk/by-partuuid/"
                      f"00000000-0000-0000-0000-{number:012d}")
        for number in range(1, 7)
    }
    record = {"current": "A", "devices": devices}
    return record, f"console=tty1 root=PARTUUID={devices['4'].rsplit('/', 1)[-1]} rauc.slot=A panic=10"


def test_ota_runtime_context_requires_exact_disk_identity_and_running_slot(tmp_path):
    record, cmdline = _valid_runtime_partition_context()
    assert ota.validate_runtime_context(record, cmdline) == record
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "context.json").write_text(json.dumps(record))
    boot = tmp_path / "cmdline"
    boot.write_text(cmdline)
    assert ota.load_runtime_context(runtime=runtime, cmdline_path=boot) == record


@pytest.mark.parametrize("kind", [
    "missing_partition", "extra_partition", "duplicate_partition",
    "noncanonical_path", "numeric_device", "invalid_slot", "wrong_current",
    "extra_root", "root_other_disk", "wrong_slot", "extra_context_key",
    "bool_current", "list_devices",
])
def test_ota_runtime_context_rejects_substitutions(kind):
    import copy
    record, cmdline = _valid_runtime_partition_context()
    record = copy.deepcopy(record)
    if kind == "missing_partition":
        del record["devices"]["6"]
    elif kind == "extra_partition":
        record["devices"]["7"] = record["devices"]["6"]
    elif kind == "duplicate_partition":
        record["devices"]["6"] = record["devices"]["5"]
    elif kind == "noncanonical_path":
        record["devices"]["6"] = "/tmp/untrusted-REDUXCAP"
    elif kind == "numeric_device":
        record["devices"]["6"] = 0
    elif kind == "invalid_slot":
        record["current"] = "C"
    elif kind == "wrong_current":
        record["current"] = "B"
    elif kind == "extra_root":
        cmdline += " root=/dev/mmcblk0p4"
    elif kind == "root_other_disk":
        cmdline = cmdline.replace("00000000-0000-0000-0000-000000000004",
                                  "00000000-0000-0000-0000-000000000099")
    elif kind == "wrong_slot":
        cmdline = cmdline.replace("rauc.slot=A", "rauc.slot=B")
    elif kind == "extra_context_key":
        record["allow_unverified"] = True
    elif kind == "bool_current":
        record["current"] = True
    elif kind == "list_devices":
        record["devices"] = []
    with pytest.raises((ValueError, TypeError)):
        ota.validate_runtime_context(record, cmdline)


def test_ota_runtime_context_refuses_bogus_or_redirected_records(tmp_path):
    record, cmdline = _valid_runtime_partition_context()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    boot = tmp_path / "cmdline"
    boot.write_text(cmdline)
    target = tmp_path / "private"
    target.write_text(json.dumps(record))
    (runtime / "context.json").symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        ota.load_runtime_context(runtime=runtime, cmdline_path=boot)
    (runtime / "context.json").unlink()
    (runtime / "context.json").write_bytes(b" " * 8193)
    with pytest.raises((OSError, ValueError)):
        ota.load_runtime_context(runtime=runtime, cmdline_path=boot)
    assert json.loads(target.read_text()) == record
