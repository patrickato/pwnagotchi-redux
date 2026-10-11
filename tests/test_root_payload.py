"""Assembled mounted root checks; fixtures never boot or mount real disks."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("redux_root_payload", ROOT / "image/root_payload.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def slot(tmp_path, name="lite", profile="lite"):
    root = tmp_path / name
    for item in gate._MUST_EXIST:
        path = root / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("installed fixture\n")
    systemd = root / "etc/systemd/system"
    for unit, (group, *directives) in gate._UNITS.items():
        target = systemd / unit
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("[Unit]\n[Service]\n" + "\n".join(directives) + "\n")
        enabled = systemd / group / unit
        enabled.parent.mkdir(parents=True, exist_ok=True)
        enabled.symlink_to("../" + unit)
    for disabled in ("bettercap.service", "resize2fs_once.service"):
        (systemd / disabled).symlink_to("/dev/null")
    config = root / "etc/redux"
    config.mkdir(parents=True)
    live = config / "live.toml"
    live.write_text(
        '[live]\nstate_dir = "/captures/redux"\n'
        'capture_dir = "/captures/incoming"\nactive_dir = "/captures/active"\n'
        'pipeline_database = "/captures/jobs.db"\nallow_connected_capture = false\n'
    )
    pipe = config / "pipeline.toml"
    pipe.write_text(
        '[pipeline]\ninputs = ["/captures/incoming"]\n'
        'output_dir = "/captures/ready"\ndatabase = "/captures/jobs.db"\n'
        '[audit]\nenabled = false\n'
    )
    live.chmod(0o600)
    pipe.chmod(0o600)
    (root / "captures").mkdir()
    (root / "etc/overlayroot.conf").write_text('overlayroot="tmpfs:recurse=0"\n')
    (root / "etc/fstab").write_text(
        "ROOTDEV / ext4 defaults 0 1\n" +
        ("LABEL=REDUXCAP /captures ext4 rw,noatime 0 2\n"
         if profile == "lite" else "/dev/null /boot/firmware vfat defaults 0 0\n")
    )
    if profile == "lite":
        (systemd / "rauc.service").symlink_to("/dev/null")
    else:
        rauc = root / "etc/rauc"
        rauc.mkdir()
        (rauc / "keyring.pem").write_text("fixture public certificate")
        (rauc / "system.conf").symlink_to("/run/redux-ota/system.conf")
    return root


def test_lite_assembled_root_accepts_runtime_config_and_enabled_services(tmp_path):
    result = gate.verify(slot(tmp_path))
    assert result["profile"] == "lite"
    assert result["slots_checked"] == 1
    assert result["root_payload_verified"] is True
    assert result["physical_boot_verified"] is False


def test_ab_checks_both_slots_individually(tmp_path):
    a = slot(tmp_path, "A", "ab")
    b = slot(tmp_path, "B", "ab")
    report = gate.verify(a, profile="ab", other_root=b)
    assert report["slots_checked"] == 2
    assert report["runtime_files_checked"] == 2 * len(gate._MUST_EXIST)
    (b / "opt/redux/redux/crack/ingest.py").unlink()
    with pytest.raises(ValueError, match="missing"):
        gate.verify(a, profile="ab", other_root=b)


@pytest.mark.parametrize("filename", gate._MUST_EXIST)
def test_missing_runtime_payload_blocks_export(tmp_path, filename):
    root = slot(tmp_path)
    (root / filename).unlink()
    with pytest.raises(ValueError, match="missing"):
        gate.verify(root)


@pytest.mark.parametrize("unit", sorted(gate._UNITS))
def test_missing_or_misdirected_service_enable_link_blocks_export(tmp_path, unit):
    root = slot(tmp_path)
    group = gate._UNITS[unit][0]
    link = root / "etc/systemd/system" / group / unit
    link.unlink()
    with pytest.raises(ValueError, match="not enabled"):
        gate.verify(root)
    link.symlink_to("/dev/null")
    with pytest.raises(ValueError, match="outside expected unit"):
        gate.verify(root)


@pytest.mark.parametrize("unit", ("bettercap.service", "resize2fs_once.service", "rauc.service"))
def test_unmasked_other_owner_or_legacy_service_blocks_lite(tmp_path, unit):
    root = slot(tmp_path)
    mask = root / "etc/systemd/system" / unit
    mask.unlink()
    mask.write_text("[Service]\nExecStart=/bin/true\n")
    with pytest.raises(ValueError, match="mask"):
        gate.verify(root)


@pytest.mark.parametrize("path,old,new", (
    ("etc/redux/live.toml", "/captures/active", "/tmp/active"),
    ("etc/redux/live.toml", "allow_connected_capture = false",
     "allow_connected_capture = true"),
    ("etc/redux/pipeline.toml", '"/captures/incoming"', '"/tmp/incoming"'),
    ("etc/redux/pipeline.toml", "enabled = false", "enabled = true"),
    ("etc/fstab", "LABEL=REDUXCAP", "LABEL=someone-else"),
    ("etc/overlayroot.conf", "tmpfs:recurse=0", "disabled"),
))
def test_inconsistent_storage_or_capture_policy_rejected(tmp_path, path, old, new):
    root = slot(tmp_path)
    file = root / path
    file.write_text(file.read_text().replace(old, new))
    with pytest.raises(ValueError):
        gate.verify(root)


def test_world_readable_config_and_legacy_resize_are_blocked(tmp_path):
    root = slot(tmp_path)
    (root / "etc/redux/live.toml").chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        gate.verify(root)
    (root / "etc/redux/live.toml").chmod(0o600)
    old = root / "etc/init.d/resize2fs_once"
    old.parent.mkdir(parents=True)
    old.write_text("old root resize script")
    with pytest.raises(ValueError, match="legacy root resize"):
        gate.verify(root)


def test_ab_refuses_lite_fstab_and_missing_rauc_trust(tmp_path):
    a = slot(tmp_path, "A", "ab")
    b = slot(tmp_path, "B", "ab")
    with (b / "etc/fstab").open("a") as out:
        out.write("LABEL=REDUXCAP /captures ext4 rw 0 2\n")
    with pytest.raises(ValueError, match="generator"):
        gate.verify(a, profile="ab", other_root=b)
    (b / "etc/fstab").write_text("ROOTDEV / ext4 defaults 0 1\n")
    (b / "etc/rauc/keyring.pem").unlink()
    with pytest.raises(ValueError, match="missing"):
        gate.verify(a, profile="ab", other_root=b)


def test_root_gate_cli_is_readonly_and_fails_on_missing_service(tmp_path):
    root = slot(tmp_path)
    argv = [sys.executable, str(ROOT / "image/root_payload.py"),
            "--profile", "lite", "--root", str(root)]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["root_payload_verified"] is True
    (root / "etc/systemd/system/redux-live.service").unlink()
    failed = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    assert failed.returncode != 0 and "missing" in failed.stderr


def test_image_install_unit_templates_satisfy_root_contract_directives():
    units = ROOT / "image/stage-redux/00-redux/files"
    for unit, (_, *directives) in gate._UNITS.items():
        if unit == "redux.service":
            source = (units / unit).read_text() + (
                ROOT / "image/stage-redux/10-overlay/00-run.sh").read_text()
        elif unit == "redux-live.service":
            source = (units / unit).read_text()
        else:
            source = (ROOT / "systemd" / unit).read_text()
        for directive in directives:
            if unit == "redux.service" and directive.startswith("Environment=REDUX_STATE_DIR"):
                # The overlay stage injects this directive using sed.
                assert "Environment=REDUX_STATE_DIR=/captures/boot" in source
            elif unit != "redux-capture-ingest.timer":
                assert directive in source, (unit, directive)


def test_prepare_gate_wires_root_before_boot_in_lite_and_ab():
    source = (ROOT / "image/prepare_boot_gate.py").read_text()
    assert source.count("redux-root-payload.py") >= 3
    assert '--other-root "$other"' in source
    assert "--profile lite --root" in source
