"""Offline-only regression tests for the opt-in Codex autopilot wrapper."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "redux-autopilot.py"
INSTALLER = ROOT / "scripts" / "install-redux-autopilot.sh"


def module():
    spec = importlib.util.spec_from_file_location("redux_autopilot", SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_default_plan_is_offline_and_does_not_run_agent(monkeypatch, capsys):
    instance = module()
    monkeypatch.setattr(instance, "invoke", lambda *args, **kw: pytest.fail("network or subprocess attempted"))
    monkeypatch.setattr(instance, "cycle", lambda: pytest.fail("agent attempted"))
    assert instance.main(["plan"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["pi_access"] is False
    assert data["sudo"] is False
    assert data["radio_changes"] is False
    assert data["card_changes"] is False
    assert "non_force_push" in data["publishing"]


def test_cycle_requires_explicit_local_opt_in():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "cycle"],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "requires --enable-agent" in result.stderr


def test_source_path_allowlist_is_strict(tmp_path):
    instance = module()
    filename = tmp_path / "redux" / "new_worker.py"
    filename.parent.mkdir()
    filename.write_text("# source only\n")
    assert instance.allowable(tmp_path, ["redux/new_worker.py"])[0]
    assert not instance.allowable(tmp_path, ["scripts/redux-autopilot.py"])[0]
    assert not instance.allowable(tmp_path, ["scripts/redux-field-bridge.py"])[0]
    assert not instance.allowable(tmp_path, ["scripts/freeze-redux-field-bridge.py"])[0]
    assert not instance.allowable(tmp_path, ["scripts/redux-field-feedback.py"])[0]
    assert not instance.allowable(tmp_path, ["scripts/redux-field-doctor.py"])[0]
    assert not instance.allowable(tmp_path, ["scripts/configure-redux-codex-feedback.py"])[0]
    assert not instance.allowable(tmp_path, ["redux/field_feedback.py"])[0]
    assert not instance.allowable(tmp_path, ["redux/field_feedback_contract.py"])[0]
    assert not instance.allowable(tmp_path, ["docs/FIELD_FEEDBACK.md"])[0]
    assert not instance.allowable(tmp_path, ["scripts/install-redux-field-bridge.sh"])[0]
    assert not instance.allowable(tmp_path, [".github/workflows/new.yml"])[0]
    assert not instance.allowable(tmp_path, ["AGENTS.md"])[0]
    assert not instance.allowable(tmp_path, ["redux/../.ssh/id_ed25519"])[0]
    assert not instance.allowable(tmp_path, ["redux/missing.py"])[0]
    assert not instance.allowable(tmp_path, ["redux/new_worker.py"] * 36)[0]
    link = tmp_path / "tests" / "linked.py"
    link.parent.mkdir()
    link.symlink_to(filename)
    assert not instance.allowable(tmp_path, ["tests/linked.py"])[0]
    # Final component need not itself be a symlink to escape an allowed tree.
    linked_directory = tmp_path / "redux" / "alias"
    linked_directory.symlink_to(tmp_path)
    assert not instance.allowable(tmp_path, ["redux/alias/other.py"])[0]
    assert instance.allowable(tmp_path, ["redux/alias/other.py"])[1] == "symlinked_parent"
    filename.write_bytes(b"x" * (instance.MAX_SINGLE_BYTES + 1))
    assert not instance.allowable(tmp_path, ["redux/new_worker.py"])[0]


def test_git_porcelain_parsing_refuses_deletions_and_renames(tmp_path):
    instance = module()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Autopilot Fixture"], cwd=tmp_path, check=True)
    directory = tmp_path / "redux"
    directory.mkdir()
    original = directory / "original.py"
    original.write_text("ORIGINAL = 1\n")
    subprocess.run(["git", "add", "--", "redux/original.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    original.write_text("ORIGINAL = 2\n")
    assert instance.changed_names(tmp_path) == ["redux/original.py"]
    original.unlink()
    with pytest.raises(ValueError, match="rename_copy_delete_or_conflict"):
        instance.changed_names(tmp_path)


def test_failed_preflight_does_not_try_to_access_pi(tmp_path, monkeypatch, capsys):
    instance = module()
    monkeypatch.setattr(instance, "STATE", tmp_path / "private")
    monkeypatch.setattr(instance.shutil, "which",
                        lambda name: None if name == "codex" else "/usr/bin/" + name)
    monkeypatch.setattr(instance, "invoke",
                        lambda *args, **kw: pytest.fail("subprocess attempted"))
    assert instance.cycle() == 2
    record = json.loads(capsys.readouterr().out)
    assert record["reason"] == "codex_cli_unavailable"
    assert record["scope"] == "offline_source_only"
    assert (tmp_path / "private" / "last.json").stat().st_mode & 0o777 == 0o600


def test_installer_has_valid_shell_syntax():
    result = subprocess.run(["bash", "-n", str(INSTALLER)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_codex_approval_flags_are_top_level_not_exec_options():
    instance = module()
    argv = instance.CODEX_COMMAND
    assert argv == ("codex", "--sandbox", "workspace-write",
                    "--ask-for-approval", "never", "exec", "-")
    assert argv.index("--ask-for-approval") < argv.index("exec")
    assert "dangerously-bypass-approvals-and-sandbox" not in argv


def test_cloned_policy_is_fresh_bounded_and_no_symlink(tmp_path):
    instance = module()
    docs = tmp_path / "docs"
    docs.mkdir()
    policy = docs / "AUTOPILOT_AGENT.md"
    policy.write_text("PICK P0 FROM FRESH GITHUB QUEUE\n")
    assert instance.cloned_policy(tmp_path).startswith("PICK P0")
    policy.write_bytes(b"x" * (16 * 1024 + 1))
    with pytest.raises(ValueError, match="remote_agent_policy_invalid"):
        instance.cloned_policy(tmp_path)
    policy.unlink()
    policy.symlink_to(ROOT / "docs" / "AUTOPILOT_AGENT.md")
    with pytest.raises(ValueError, match="remote_agent_policy_invalid"):
        instance.cloned_policy(tmp_path)
    policy.unlink()
    docs.rmdir()
    docs.symlink_to(ROOT / "docs", target_is_directory=True)
    with pytest.raises(ValueError, match="remote_agent_policy_invalid"):
        instance.cloned_policy(tmp_path)


def test_timing_migration_is_two_hour_user_only_and_idempotent(tmp_path):
    """Run --retime with fake systemd commands, never touching user's units."""
    import os
    import shutil
    home = tmp_path / "home"
    src = home / "redux-workbench-src" / "scripts"
    src.mkdir(parents=True)
    local = src / "install-redux-autopilot.sh"
    shutil.copyfile(INSTALLER, local)
    unit_dir = home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    timer = unit_dir / "redux-autopilot.timer"
    timer.write_text("[Timer]\nOnCalendar=*-*-* 01,07,13,19:00:00\nUnit=redux-autopilot.service\n")
    bindir = tmp_path / "fake-systemd"
    bindir.mkdir()
    for name in ("systemctl", "systemd-analyze"):
        binary = bindir / name
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o700)
    env = dict(os.environ, HOME=str(home), PATH=str(bindir) + os.pathsep + os.environ["PATH"])
    for _ in range(2):
        completed = subprocess.run(
            ["bash", str(local), "--retime"], env=env, cwd=src.parent,
            capture_output=True, text=True, timeout=10, check=False)
        assert completed.returncode == 0, completed.stderr
    assert "OnCalendar=*-*-* 00,02,04,06,08,10,12,14,16,18,20,22:00:00" in timer.read_text()
    assert "01,07,13,19" not in timer.read_text()


def test_timing_migration_refuses_unrecognized_timer(tmp_path):
    import os
    import shutil
    home = tmp_path / "home"
    scripts = home / "redux-workbench-src" / "scripts"
    scripts.mkdir(parents=True)
    local = scripts / "install-redux-autopilot.sh"
    shutil.copyfile(INSTALLER, local)
    units = home / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    timer = units / "redux-autopilot.timer"
    timer.write_text("[Timer]\nOnCalendar=hourly\n")
    before = timer.read_bytes()
    env = dict(os.environ, HOME=str(home))
    result = subprocess.run(["bash", str(local), "--retime"], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert timer.read_bytes() == before


def valid_feedback():
    """A complete field schema with no real Pi data."""
    return {
        "schema": 1, "format": "redux-field-feedback-v1",
        "provenance": "saved_unsigned_read_only_observation",
        "snapshot_freshness": "UNKNOWN",
        "physical_acceptance": "UNKNOWN", "release_qualification": "UNKNOWN",
        "radios": [],
        "display": {"state": None, "blockers": []},
        "processing": {"worker_available": None,
                       "worker_fresh_at_collection": None,
                       "ledger_total": None, "by_status": None,
                       "last_scan": None},
        "storage_free_band": None, "runtime_state_at_collection": None,
        "signals": [],
        "does_not_prove": ["native_wifi_reception",
                           "handshake_authenticity",
                           "physical_touch", "rollback_recovery"],
    }


def test_opt_in_private_hardware_summary_is_strictly_offline():
    instance = module()
    from types import SimpleNamespace
    received = []
    def fake_runner(argv, **kwargs):
        received.append(argv)
        assert "ssh" not in argv
        assert argv[-1] == "--latest-private"
        assert kwargs["timeout"] == 30
        return SimpleNamespace(returncode=0, stdout=json.dumps(valid_feedback()))
    encoded = instance.local_sanitized_hardware_feedback(runner=fake_runner)
    assert len(received) == 1
    assert json.loads(encoded) == valid_feedback()
    assert json.loads(encoded)["release_qualification"] == "UNKNOWN"


@pytest.mark.parametrize("payload", [
    {"schema":1,"format":"redux-field-feedback-v1",
     "provenance":"saved_unsigned_read_only_observation",
     "physical_acceptance":"PASS","release_qualification":"UNKNOWN"},
    {"schema":1,"format":"redux-field-feedback-v1",
     "provenance":"untrusted","physical_acceptance":"UNKNOWN",
     "release_qualification":"UNKNOWN"},
    {"schema":2,"format":"redux-field-feedback-v1",
     "provenance":"saved_unsigned_read_only_observation",
     "physical_acceptance":"UNKNOWN","release_qualification":"UNKNOWN"},
])
def test_codex_feedback_rejects_hardware_pass_and_invalid_schema(payload):
    instance = module()
    from types import SimpleNamespace
    assert instance.local_sanitized_hardware_feedback(
        runner=lambda *a, **kw: SimpleNamespace(returncode=0,
                                                 stdout=json.dumps(payload))) is None


def test_feedback_remains_explicit_opt_in_by_default():
    instance = module()
    assert instance.cycle.__kwdefaults__["with_sanitized_field_feedback"] is False


def test_feedback_filter_rejects_extra_raw_private_data_and_nested_commands():
    from types import SimpleNamespace
    instance = module()
    payload = valid_feedback()
    payload["raw_lan_ip"] = "SECRET_PRIVATE_ADDRESS"
    result = instance.local_sanitized_hardware_feedback(runner=lambda *a, **kw:
                SimpleNamespace(returncode=0, stdout=json.dumps(payload)))
    assert result is None
    payload = valid_feedback()
    payload["radios"] = [{"device_name": "SECRET_INTERFACE"}]
    assert instance.local_sanitized_hardware_feedback(runner=lambda *a, **kw:
           SimpleNamespace(returncode=0, stdout=json.dumps(payload))) is None


def test_feedback_filter_rejects_duplicate_fields_and_nonfinite_constants():
    from types import SimpleNamespace
    instance = module()
    base = json.dumps(valid_feedback())
    duplicate = base.replace('"schema": 1', '"schema": 1, "schema": 1', 1)
    malformed = [duplicate, base.replace('"schema": 1', '"schema": NaN', 1)]
    for stdout in malformed:
        assert instance.local_sanitized_hardware_feedback(runner=lambda *a, **kw:
            SimpleNamespace(returncode=0, stdout=stdout)) is None


def test_feedback_filter_rejects_inconsistent_metadata_signals():
    from types import SimpleNamespace
    instance = module()
    payload = valid_feedback()
    payload["signals"] = ["usb_radiotap_not_reception_proof"]
    assert instance.local_sanitized_hardware_feedback(runner=lambda *a, **kw:
           SimpleNamespace(returncode=0, stdout=json.dumps(payload))) is None



def test_full_offline_verification_keeps_expected_test_order():
    instance = module()
    observed = []
    def fake(argv, **kwargs):
        observed.append((argv, kwargs["timeout"]))
        assert "ssh" not in argv
        assert "sudo" not in argv
        return 0, ""
    assert instance.independent_verification("/synthetic-checkout", {},
                                             invoker=fake) is None
    assert observed[0][0] == ["python3", "-m", "pytest", "-q"]
    assert observed[1][0] == ["bash", "image/ci.sh"]
    assert observed[2][0][:4] == ["python3", "-m", "compileall", "-q"]
    assert observed[3][0] == ["git", "diff", "--check"]
    assert all(0 < timeout <= 600 for _, timeout in observed)


def test_failed_python_tests_trigger_one_repair_and_full_reverification():
    instance = module()
    events = []
    def inspector(cwd, revision):
        events.append("inspect")
        return None, ["redux/source.py", "tests/test_source.py"]
    failures = iter(["python_tests_failed", None])
    def verifier(cwd, env):
        events.append("verify")
        return next(failures)
    def agent(cwd, instruction, env, *, timeout):
        events.append("repair")
        assert timeout == 600
        assert "python_tests_failed" in instruction
        assert "ONE SOURCE-ONLY REPAIR PASS" in instruction
        assert "ssh" not in instruction.lower().split("your previous")[0]
        return True
    status, paths = instance.validate_with_one_repair(
        "/synthetic", "a" * 40, {}, {}, "Safe offline source engineering.",
        verifier=verifier, agent=agent, inspector=inspector)
    assert status is None
    assert paths == ["redux/source.py", "tests/test_source.py"]
    assert events == ["inspect", "verify", "repair", "inspect", "verify"]


def test_repair_always_stops_after_second_test_failure():
    instance = module()
    repairs = []
    def agent(*args, **kwargs):
        repairs.append(1)
        return True
    status, paths = instance.validate_with_one_repair(
        "/synthetic", "a" * 40, {}, {}, "offline",
        verifier=lambda *args: "python_tests_failed",
        agent=agent, inspector=lambda *args: (None, ["tests/test_case.py"]))
    assert status == "tests_failed_after_repair"
    assert len(repairs) == 1
    assert paths == ["tests/test_case.py"]


def test_repair_never_proceeds_after_history_or_file_guard_failure():
    instance = module()
    for reason in ("agent_changed_git_history", "restricted_path",
                   "unsafe_git_status", "symlinked_parent"):
        status, paths = instance.validate_with_one_repair(
            "/synthetic", "a" * 40, {}, {}, "offline",
            verifier=lambda *args: pytest.fail("verification attempted"),
            agent=lambda *args, **kwargs: pytest.fail("repair attempted"),
            inspector=lambda *args: (reason, []))
        assert status == reason and paths == []


def test_repair_cannot_override_postrepair_protected_path_rejection():
    instance = module()
    guards = iter([(None, ["redux/safe.py"]),
                   ("restricted_path", [])])
    status, paths = instance.validate_with_one_repair(
        "/synthetic", "a" * 40, {}, {}, "offline",
        verifier=lambda *args: "python_tests_failed",
        agent=lambda *args, **kw: True,
        inspector=lambda *args: next(guards))
    assert status == "restricted_path"
    assert paths == []


def test_no_repair_after_first_success_and_no_change_is_idle():
    instance = module()
    called = []
    def no_agent(*a, **kw):
        called.append(True)
        return False
    result = instance.validate_with_one_repair(
        "/synthetic", "a" * 40, {}, {}, "offline",
        verifier=lambda *args: None, agent=no_agent,
        inspector=lambda *args: (None, ["redux/safe.py"]))
    assert result == (None, ["redux/safe.py"])
    no_change = instance.validate_with_one_repair(
        "/synthetic", "a" * 40, {}, {}, "offline",
        verifier=lambda *args: pytest.fail("tests attempted"),
        agent=no_agent, inspector=lambda *args: (None, []))
    assert no_change == (None, [])
    assert called == []


def test_repair_command_remains_sandboxed_and_noninteractive():
    instance = module()
    from types import SimpleNamespace
    calls = []
    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)
    assert instance.run_codex("/synthetic", "offline", {},
                              timeout=600, runner=runner)
    assert len(calls) == 1
    argv, kw = calls[0]
    assert tuple(argv) == instance.CODEX_COMMAND
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert argv[argv.index("--ask-for-approval") + 1] == "never"
    assert kw["timeout"] == 600
    assert kw["stderr"] is subprocess.DEVNULL
    assert kw["stdout"] is subprocess.DEVNULL
