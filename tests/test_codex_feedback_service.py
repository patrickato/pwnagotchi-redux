"""No-Pi local systemd fixture tests for an explicit data-transfer consent gate."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/configure-redux-codex-feedback.py"


def loaded():
    spec = importlib.util.spec_from_file_location("redux_codex_feedback_local", SCRIPT)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def fixture(tmp_path):
    mod = loaded()
    user = tmp_path / ".config/systemd/user"
    user.mkdir(parents=True)
    unit = user / "redux-autopilot.service"
    unit.write_text("[Unit]\nDescription=Source-only agent\n"
                    "[Service]\nType=oneshot\n"
                    "WorkingDirectory=%h/redux-workbench-src\n"
                    + mod.EXPECTED + "\n"
                    "TimeoutStartSec=100min\n"
                    "NoNewPrivileges=yes\n"
                    "Environment=PATH=/usr/bin:/bin\n")
    unit.chmod(0o600)
    return mod, unit


def test_plan_performs_no_network_or_local_file_actions(tmp_path, monkeypatch, capsys):
    mod = loaded()
    monkeypatch.setattr(mod, "private_unit_text",
                        lambda *args: pytest.fail("plan accessed private service"))
    assert mod.main(["--plan"]) == 0
    scope = json.loads(capsys.readouterr().out)
    assert scope["requires_explicit_model_data_consent"]
    assert scope["enabled_by_default"] is False
    assert scope["pi_contact"] is False
    assert scope["raw_capture_transfer"] is False
    assert scope["field_approval_changed"] is False


def test_consent_required_before_any_change(tmp_path):
    mod, unit = fixture(tmp_path)
    original = unit.read_bytes()
    invoked = []
    with pytest.raises(ValueError, match="explicit_sanitized_model_consent_required"):
        mod.configure(enable=True, acknowledged=False, service=unit,
                      reload=lambda: invoked.append(1))
    assert invoked == []
    assert unit.read_bytes() == original


def test_explicit_toggle_is_idempotent_and_only_changes_execstart(tmp_path):
    mod, unit = fixture(tmp_path)
    original = unit.read_bytes()
    count = []
    def reload():
        count.append(1)
        return True
    success = mod.configure(enable=True, acknowledged=True, service=unit,
                            reload=reload)
    assert success["status"] == "configured"
    assert success["sanitized_model_feedback"] == "on"
    assert success["pi_contact"] is False
    assert unit.read_text().count(mod.WITH_FEEDBACK) == 1
    assert mod.EXPECTED in unit.read_text()
    assert unit.read_text().count("NoNewPrivileges=yes") == 1
    assert unit.read_text().count("Environment=PATH") == 1
    assert mod.configure(enable=True, acknowledged=True, service=unit,
                         reload=reload)["status"] == "already_configured"
    assert len(count) == 1
    off = mod.configure(enable=False, acknowledged=False, service=unit,
                        reload=reload)
    assert off["sanitized_model_feedback"] == "off"
    assert unit.read_bytes() == original
    assert len(count) == 2


def test_failed_reload_restores_local_service_and_refuses_to_report_success(tmp_path):
    mod, unit = fixture(tmp_path)
    initial = unit.read_bytes()
    calls = []
    def reload():
        calls.append(1)
        return len(calls) != 1
    with pytest.raises(ValueError, match="systemd_reload_failed"):
        mod.configure(enable=True, acknowledged=True, service=unit,
                      reload=reload)
    assert calls == [1, 1]
    assert unit.read_bytes() == initial


def test_bad_service_commands_are_not_overwritten(tmp_path):
    mod, unit = fixture(tmp_path)
    for payload in [
        unit.read_text().replace(mod.EXPECTED, "ExecStart=/bin/bash /unsafe"),
        unit.read_text() + "ExecStartPre=/bin/echo unsafe\n",
        unit.read_text() + "ExecStart=/bin/true\n",
    ]:
        unit.write_text(payload)
        unit.chmod(0o600)
        with pytest.raises(ValueError, match="unrecognized_codex_service"):
            mod.configure(enable=True, acknowledged=True, service=unit,
                          reload=lambda: pytest.fail("daemon-reload attempted"))


def test_symlink_and_open_permissions_are_not_trusted(tmp_path):
    mod, unit = fixture(tmp_path)
    unit.chmod(0o644)
    with pytest.raises(ValueError, match="untrusted_codex_service"):
        mod.private_unit_text(unit)
    unit.chmod(0o600)
    alias = unit.parent / "alias.service"
    alias.symlink_to(unit)
    with pytest.raises(OSError):
        mod.private_unit_text(alias)


def test_missing_approval_flag_cli_returns_fixed_redacted_status(monkeypatch, capsys):
    mod = loaded()
    monkeypatch.setattr(mod, "configure", lambda **kwargs:
                        (_ for _ in ()).throw(ValueError(
                            "PRIVATE_HOST_KEY_PATH")))
    assert mod.main(["--enable"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert "PRIVATE_" not in json.dumps(result)


def test_local_configure_cli_requires_exactly_one_operation():
    run = subprocess.run([sys.executable, str(SCRIPT)],
                         cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert run.returncode == 2
    assert "required" in run.stderr.lower()
