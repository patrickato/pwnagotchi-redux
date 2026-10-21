"""Offline test of local code-freeze migration. No Pi, SSH or real units."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "freeze-redux-field-bridge.py"


def tool():
    spec = importlib.util.spec_from_file_location("redux_freeze_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def synthetic(monkeypatch, tmp_path):
    mod = tool()
    home = tmp_path / "home"
    checkout = home / "redux-workbench-src"
    checkout.mkdir(parents=True)
    state = home / ".local" / "state" / "redux-field-bridge"
    state.mkdir(parents=True, mode=0o700)
    frozen = home / ".local" / "share" / "redux-field-bridge" / "approved-worktree"
    units = home / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    service = units / "redux-field-bridge.service"
    service.write_text("[Unit]\nDescription=Redux immutable Pi reader\n[Service]\n"
                       + mod.OLD_WORKDIR + "\n" + mod.OLD_EXEC
                       + "\nNoNewPrivileges=yes\n")
    service.chmod(0o600)
    timer = units / "redux-field-bridge.timer"
    timer.write_text("[Timer]\nOnCalendar=*-*-* *:20:00\n"
                     "Unit=redux-field-bridge.service\n")
    timer.chmod(0o600)
    monkeypatch.setattr(Path, "home", classmethod(lambda _: home))
    for name, val in (("ROOT",checkout),("STATE",state),("FREEZE",frozen),
                      ("SERVICE",service),("TIMER",timer)):
        monkeypatch.setattr(mod,name,val)
    pin={"source_revision":"a"*40, "launcher_sha256":"b"*64,
         "remote_bundle_sha256":"c"*64}
    monkeypatch.setattr(mod, "approval", lambda: pin)
    monkeypatch.setattr(mod, "frozen_matches", lambda p: True)
    return mod, pin, service, timer, frozen


def test_plan_never_reads_private_approval_or_runs_hardware(monkeypatch, capsys):
    mod = tool()
    monkeypatch.setattr(mod, "migration", lambda: pytest.fail("migration attempted"))
    assert mod.main(["--plan"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ssh_contact"] is False
    assert output["changes_pi"] is False
    assert output["approval_extended"] is False


def test_unit_template_refuses_missing_or_repeated_execstart(tmp_path, monkeypatch):
    mod, pin, service, timer, frozen = synthetic(monkeypatch, tmp_path)
    expected, replaced = mod.unit_text()
    assert mod.OLD_EXEC in expected
    assert mod.NEW_EXEC in replaced and mod.OLD_EXEC not in replaced
    assert mod.NEW_WORKDIR in replaced and mod.OLD_WORKDIR not in replaced
    service.write_text("[Service]\nExecStart=/bin/sh -c 'unapproved'\n")
    with pytest.raises(ValueError, match="unrecognized_field_service"):
        mod.unit_text()
    assert not frozen.exists()


def test_refuses_preexisting_frozen_worktree_without_changing_timer(tmp_path, monkeypatch):
    mod, pin, service, timer, frozen = synthetic(monkeypatch, tmp_path)
    frozen.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    frozen.mkdir()
    monkeypatch.setattr(mod, "command",
                        lambda *a, **k: pytest.fail("command attempted"))
    with pytest.raises(ValueError, match="frozen_worktree_already_exists"):
        mod.migration()
    assert mod.OLD_EXEC in service.read_text()


def test_pins_approved_commit_and_rebinds_only_local_systemd_service(tmp_path, monkeypatch):
    mod, pin, service, timer, frozen = synthetic(monkeypatch, tmp_path)
    calls = []
    def local(argv, **kwargs):
        calls.append(list(argv))
        assert argv[0] in ("git", "systemctl")
        assert not any(x in argv for x in ("ssh", "sudo", "iw", "nmcli"))
        if argv[:3] == ["git", "cat-file", "-t"]:
            assert argv[3] == pin["source_revision"]
            return SimpleNamespace(returncode=0, stdout="commit\n")
        if argv[:3] == ["git", "worktree", "add"]:
            assert argv[-1] == pin["source_revision"]
            frozen.mkdir()
            return SimpleNamespace(returncode=0, stdout="")
        if argv[:4] == ["systemctl", "--user", "is-active", "--quiet"]:
            return SimpleNamespace(returncode=3, stdout="")
        assert argv == ["systemctl", "--user", "daemon-reload"]
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(mod, "command", local)
    before_timer = timer.read_bytes()
    result = mod.migration()
    assert result["hardware_action_performed"] is False
    assert result["timer_schedule_changed"] is False
    assert result["read_only_approval_changed"] is False
    assert result["session_budget_reset"] is False
    assert mod.NEW_EXEC in service.read_text()
    assert mod.NEW_WORKDIR in service.read_text()
    assert mod.OLD_EXEC not in service.read_text()
    assert mod.OLD_WORKDIR not in service.read_text()
    assert timer.read_bytes() == before_timer
    assert len([x for x in calls if x[:3] == ["git", "worktree", "add"]]) == 1


def test_failed_reload_restores_old_service_and_approval(tmp_path, monkeypatch):
    mod, pin, service, timer, frozen = synthetic(monkeypatch, tmp_path)
    old_service = service.read_bytes()
    before_timer = timer.read_bytes()
    reload_attempts = [0]
    def local(argv, **kwargs):
        if argv[:3] == ["git", "cat-file", "-t"]:
            return SimpleNamespace(returncode=0, stdout="commit\n")
        if argv[:3] == ["git", "worktree", "add"]:
            frozen.mkdir()
            return SimpleNamespace(returncode=0, stdout="")
        if argv[:4] == ["systemctl", "--user", "is-active", "--quiet"]:
            return SimpleNamespace(returncode=3, stdout="")
        if argv == ["systemctl", "--user", "daemon-reload"]:
            reload_attempts[0]+=1
            return SimpleNamespace(returncode=1 if reload_attempts[0]==1 else 0,
                                   stdout="")
        pytest.fail("unexpected local command")
    monkeypatch.setattr(mod, "command", local)
    with pytest.raises(ValueError, match="systemd_reload_failed"):
        mod.migration()
    assert reload_attempts[0] == 2
    assert service.read_bytes() == old_service
    assert timer.read_bytes() == before_timer


def test_systemd_reload_timeout_restores_existing_service(tmp_path, monkeypatch):
    mod, pin, service, timer, frozen = synthetic(monkeypatch, tmp_path)
    original = service.read_bytes()
    calls = []
    def local(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["git", "cat-file", "-t"]:
            return SimpleNamespace(returncode=0, stdout="commit\n")
        if argv[:3] == ["git", "worktree", "add"]:
            frozen.mkdir()
            return SimpleNamespace(returncode=0, stdout="")
        if argv[:4] == ["systemctl", "--user", "is-active", "--quiet"]:
            return SimpleNamespace(returncode=3, stdout="")
        if argv == ["systemctl", "--user", "daemon-reload"]:
            if len([c for c in calls if c == argv]) == 1:
                raise ValueError("local_command_unavailable")
            return SimpleNamespace(returncode=0, stdout="")
        pytest.fail("unexpected operation")
    monkeypatch.setattr(mod, "command", local)
    with pytest.raises(ValueError, match="systemd_reload_failed"):
        mod.migration()
    assert service.read_bytes() == original
    assert timer.read_text().count("OnCalendar=*-*-* *:20:00") == 1


def test_refuses_currently_active_observer(tmp_path, monkeypatch):
    mod, pin, service, timer, frozen = synthetic(monkeypatch, tmp_path)
    def local(argv, **kwargs):
        if argv[:3] == ["git", "cat-file", "-t"]:
            return SimpleNamespace(returncode=0, stdout="commit\n")
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(mod, "command", local)
    with pytest.raises(ValueError, match="field_observer_currently_running"):
        mod.migration()
    assert mod.OLD_EXEC in service.read_text()
    assert not frozen.exists()


def test_malformed_approval_never_prints_identifiers(tmp_path, monkeypatch, capsys):
    mod, pin, service, timer, frozen = synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "approval",
                        lambda: (_ for _ in ()).throw(ValueError(
                            "PRIVATE_SSH_KEY_VALUE")))
    assert mod.main(["--migrate"]) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["reason"] == "migration_failed"
    assert "PRIVATE" not in json.dumps(output)
    assert not frozen.exists()
