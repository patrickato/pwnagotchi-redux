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
    (root / "usr/share/redux/packages.tsv").write_text("python3\t3.11.2-1\n")
    systemd = root / "etc/systemd/system"
    # The field image ships the real, disabled native TFT unit. Rootfs
    # fixtures mimic its exact policy rather than activating any display.
    (systemd / "redux-fbui.service").write_text(
        (ROOT / "systemd/redux-fbui.service").read_text()
    )
    (root / "usr/share/redux/fbui.example.env").write_text(
        (ROOT / "config/fbui.example.env").read_text()
    )
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
        '[live]\nstate_dir = "/captures/live"\n'
        'capture_dir = "/captures/incoming"\nactive_dir = "/captures/active"\n'
        'pipeline_database = "/captures/jobs.db"\nallow_connected_capture = false\n'
        'require_usb_lease = false\n'
        'require_regulatory_country = true\n'
        'regulatory_country_file = "/etc/redux/regulatory-country"\n'
        '[phone]\nenabled = false\n'
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
        for unit, (group, *directives) in gate._AB_UNITS.items():
            target = systemd / unit
            target.write_text("[Unit]\n[Timer]\n[Service]\n" +
                              "\n".join(directives) + "\n")
            enabled = systemd / group / unit
            enabled.parent.mkdir(parents=True, exist_ok=True)
            enabled.symlink_to("../" + unit)
        for unit, directives in gate._AB_REQUIRED_UNITS.items():
            target = systemd / unit
            target.write_text("[Unit]\n[Service]\n" + "\n".join(directives) + "\n")
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


def test_live_state_dir_must_not_collide_with_unprivileged_redux_service(tmp_path):
    root = slot(tmp_path)
    baked = root / "etc/redux/live.toml"
    assert 'state_dir = "/captures/live"' in baked.read_text()
    baked.write_text(baked.read_text().replace('/captures/live', '/captures/redux'))
    with pytest.raises(ValueError, match="state_dir"):
        gate.verify(root)


def test_root_contract_matches_staged_source_live_config():
    import tomllib
    actual = tomllib.loads(
        (ROOT / "config/live.example.toml").read_text())["live"]
    assert actual["state_dir"] == "/captures/live"
    assert actual["capture_dir"] == "/captures/incoming"
    assert actual["active_dir"] == "/captures/active"


def test_ab_checks_both_slots_individually(tmp_path):
    a = slot(tmp_path, "A", "ab")
    b = slot(tmp_path, "B", "ab")
    report = gate.verify(a, profile="ab", other_root=b)
    assert report["slots_checked"] == 2
    assert report["runtime_files_checked"] == 2 * len(gate._MUST_EXIST)
    (b / "opt/redux/redux/crack/ingest.py").unlink()
    with pytest.raises(ValueError, match="missing"):
        gate.verify(a, profile="ab", other_root=b)


@pytest.mark.parametrize("unit,directive", [
    ("redux-ota-health.service", "Requires=redux.service"),
    ("redux-ota-deadline.service", "Restart=on-failure"),
])
def test_ab_requires_health_and_repeating_rollback_contract(tmp_path, unit, directive):
    a = slot(tmp_path, "A", "ab")
    b = slot(tmp_path, "B", "ab")
    target = b / "etc/systemd/system" / unit
    target.write_text(target.read_text().replace(directive + "\n", ""))
    with pytest.raises(ValueError, match=directive):
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


def test_native_tft_payload_is_required_but_never_enabled(tmp_path):
    root = slot(tmp_path)
    assert gate.verify(root)["root_payload_verified"] is True
    unit = root / "etc/systemd/system/redux-fbui.service"
    unit.write_text(unit.read_text().replace(
        "ConditionPathExists=/captures/config/fbui.env",
        "ConditionPathExists=/dev/fb1",
    ))
    with pytest.raises(ValueError, match="TFT"):
        gate.verify(root)
    unit.write_text((ROOT / "systemd/redux-fbui.service").read_text())
    wants = root / "etc/systemd/system/multi-user.target.wants"
    wants.mkdir(exist_ok=True)
    (wants / "redux-fbui.service").symlink_to("../redux-fbui.service")
    with pytest.raises(ValueError, match="autostart"):
        gate.verify(root)


def test_export_rejects_baked_in_unverified_framebuffer_config(tmp_path):
    root = slot(tmp_path)
    active = root / "captures/config/fbui.env"
    active.parent.mkdir(parents=True)
    active.write_text("REDUX_FRAMEBUFFER=/dev/fb1\n")
    with pytest.raises(ValueError, match="preconfigure"):
        gate.verify(root)


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
        elif unit in ("redux-live.service", "redux-regdom.service"):
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


@pytest.mark.parametrize("unit", sorted(gate._UNITS))
def test_image_absolute_systemctl_enable_symlinks_resolve_in_guest_root(tmp_path, unit):
    root = slot(tmp_path)
    group = gate._UNITS[unit][0]
    link = root / "etc/systemd/system" / group / unit
    link.unlink()
    link.symlink_to("/etc/systemd/system/" + unit)
    assert gate.verify(root)["root_payload_verified"] is True


@pytest.mark.parametrize("target", [
    "/etc/passwd",
    "/etc/systemd/system/other.service",
    "/usr/lib/systemd/system/redux.service",
    "../../../../../../etc/passwd",
    "../redux-live.service",
])
def test_image_service_link_cannot_escape_or_select_other_unit(tmp_path, target):
    root = slot(tmp_path)
    link = root / "etc/systemd/system/multi-user.target.wants/redux.service"
    link.unlink()
    link.symlink_to(target)
    with pytest.raises(ValueError, match="outside expected unit"):
        gate.verify(root)


def test_real_offline_systemctl_enable_link_survives_export_gate(tmp_path):
    import shutil
    executable = shutil.which("systemctl")
    if not executable:
        pytest.skip("systemctl unavailable; guest-root fixture still covers absolute links")
    root = slot(tmp_path)
    unit = "redux.service"
    target = root / "etc/systemd/system" / unit
    target.write_text(target.read_text() + "\n[Install]\nWantedBy=multi-user.target\n")
    link = root / "etc/systemd/system/multi-user.target.wants" / unit
    link.unlink()
    invocation = subprocess.run(
        [executable, "--root", str(root), "enable", unit],
        text=True, capture_output=True, timeout=10,
    )
    assert invocation.returncode == 0, invocation.stderr
    assert link.is_symlink()
    assert link.readlink() == Path("/etc/systemd/system/redux.service")
    assert gate.verify(root)["root_payload_verified"] is True


def test_field_image_rejects_unconfined_bettercap_home(tmp_path):
    root = slot(tmp_path)
    unit = root / "etc/systemd/system/redux-live.service"
    text = unit.read_text()
    assert "Environment=HOME=/captures/live" in text
    unit.write_text(text.replace("Environment=HOME=/captures/live", "Environment=HOME=/root"))
    with pytest.raises(ValueError, match="Environment=HOME=/captures/live"):
        gate.verify(root)


def test_field_image_rejects_home_sandbox_regression(tmp_path):
    root = slot(tmp_path)
    unit = root / "etc/systemd/system/redux-live.service"
    unit.write_text(unit.read_text().replace("ProtectHome=true", "ProtectHome=false"))
    with pytest.raises(ValueError, match="ProtectHome=true"):
        gate.verify(root)


def test_regulatory_country_optin_has_valid_image_contract(tmp_path):
    root = slot(tmp_path)
    country = root / "etc/redux/regulatory-country"
    assert gate.verify(root)["root_payload_verified"] is True  # absent => runtime safely disabled
    country.write_text("US\n")
    assert gate.verify(root)["root_payload_verified"] is True
    country.write_text("00\n")
    with pytest.raises(ValueError, match="invalid regulatory country"):
        gate.verify(root)
    country.write_text("US\n")
    country.chmod(0o666)
    with pytest.raises(ValueError, match="writable by group"):
        gate.verify(root)


def test_image_rejects_optout_of_country_requirement(tmp_path):
    root = slot(tmp_path)
    live = root / "etc/redux/live.toml"
    live.write_text(live.read_text().replace(
        "require_regulatory_country = true", "require_regulatory_country = false"))
    with pytest.raises(ValueError, match="require an operator-configured"):
        gate.verify(root)


def test_image_regulatory_oneshot_and_order_enforced(tmp_path):
    root = slot(tmp_path)
    unit = root / "etc/systemd/system/redux-regdom.service"
    unit.write_text(unit.read_text().replace(
        "Before=redux-live.service", "After=redux-live.service"))
    with pytest.raises(ValueError, match="Before=redux-live.service"):
        gate.verify(root)


def test_field_export_mounted_root_binds_selected_country(tmp_path):
    root = slot(tmp_path)
    file = root / "etc/redux/regulatory-country"
    with pytest.raises(ValueError, match="expected regulatory country missing"):
        gate.verify(root, expected_country="US")
    file.write_text("US\n")
    result = gate.verify(root, expected_country="US")
    assert result["regulatory_country_verified"] is True
    with pytest.raises(ValueError, match="country mismatch"):
        gate.verify(root, expected_country="CA")
    with pytest.raises(ValueError, match="invalid expected regulatory country"):
        gate.verify(root, expected_country="US; restart")


def test_field_export_checks_both_ab_slot_countries(tmp_path):
    a = slot(tmp_path, "slot_a", "ab")
    b = slot(tmp_path, "slot_b", "ab")
    for root in (a, b):
        (root / "etc/redux/regulatory-country").write_text("US\n")
    assert gate.verify(a, profile="ab", other_root=b,
                       expected_country="US")["regulatory_country_verified"] is True
    (b / "etc/redux/regulatory-country").write_text("CA\n")
    with pytest.raises(ValueError, match="country mismatch"):
        gate.verify(a, profile="ab", other_root=b, expected_country="US")


def test_root_payload_cli_rejects_mismatched_country(tmp_path):
    root = slot(tmp_path)
    (root / "etc/redux/regulatory-country").write_text("CA\n")
    command = [sys.executable, str(ROOT / "image/root_payload.py"),
               "--profile", "lite", "--root", str(root),
               "--expected-country", "US"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "country mismatch" in result.stderr


@pytest.mark.parametrize('old,new', [
    ('require_usb_lease = false', 'require_usb_lease = true'),
    ('enabled = false', 'enabled = true'),
])
def test_image_keeps_unqualified_connectivity_disabled(tmp_path, old, new):
    root = slot(tmp_path)
    config = root / 'etc/redux/live.toml'
    config.write_text(config.read_text().replace(old, new))
    with pytest.raises(ValueError, match='disabled'):
        gate.verify(root)


@pytest.mark.parametrize('payload', ['', 'not-a-package-list\n', 'python3\t1\npython3\t2\n', 'python3\tbad version\n'])
def test_invalid_installed_package_metadata_is_rejected(tmp_path,payload):
    root=slot(tmp_path)
    (root/'usr/share/redux/packages.tsv').write_text(payload)
    with pytest.raises(ValueError):gate.verify(root)


@pytest.mark.parametrize('name',['core/telemetry.py','core/processing_evidence.py','display/ui_state.py','web/status_page.py'])
def test_recent_runtime_dependencies_cannot_be_missing_from_export(tmp_path,name):
    root=slot(tmp_path)
    (root/'opt/redux/redux'/name).unlink()
    with pytest.raises(ValueError):gate.verify(root)
