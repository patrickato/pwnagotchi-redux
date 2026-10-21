"""Pure local fixtures for the private field-observer health doctor; no Pi SSH."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/redux-field-doctor.py"


def module():
    spec = importlib.util.spec_from_file_location("redux_field_doctor", SCRIPT)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def fixture(tmp_path, monkeypatch):
    obj = module()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    policy = {
        "schema":1, "host":"192.168.56.99", "key_path":"/private/never/output",
        "created": 100, "expires": 100+14*86400,
        "interval_seconds":3600, "max_sessions":40,
        "used":3, "last_attempt":100+3*3600,
        "source_revision":"a"*40, "launcher_sha256":"b"*64,
        "remote_bundle_sha256":"c"*64, "enabled":True,
    }
    (state/"recurring-approval.json").write_text(json.dumps(policy))
    (state/"recurring-approval.json").chmod(0o600)
    service=tmp_path/"redux-field-bridge.service"
    service.write_text("[Service]\n"+obj.EXPECTED_WORKDIR+"\n"
                       +obj.EXPECTED_SERVICE+"\n")
    timer=tmp_path/"redux-field-bridge.timer"
    timer.write_text("[Timer]\n"+obj.EXPECTED_TIMER+"\n"
                     +"Unit=redux-field-bridge.service\n")
    freeze=tmp_path/"freeze"
    freeze.mkdir()
    monkeypatch.setattr(obj, "FROZEN", freeze)
    return obj, state, policy, service, timer


def systemd(command, **kwargs):
    assert command[:3]==["systemctl", "--user", "show"]
    assert "ssh" not in command
    assert "journalctl" not in command
    return SimpleNamespace(returncode=0, stdout=(
        "ActiveState=inactive\nResult=success\nExecMainStatus=0\n"
        "ExecMainCode=1\n"))


def test_plan_without_private_status_remains_unknown(tmp_path, monkeypatch):
    obj, state, policy, service, timer=fixture(tmp_path, monkeypatch)
    summary=obj.inspect(state=state,service=service,timer=timer,
                        now=100+5*3600,runner=systemd)
    assert summary["network_actions_performed"] is False
    assert summary["pi_configuration_changes"] is False
    assert summary["physical_acceptance"]=="UNKNOWN"
    assert summary["approved"]["sessions_used"]==3
    assert summary["approved"]["sessions_remaining"]==37
    assert summary["approved"]["interval_minutes"]==60
    assert summary["approved"]["last_attempt_minutes_ago"]==120
    assert summary["last_recorded_collection"]["status"]=="never_recorded"
    assert summary["unit"]["hourly_timer"]=="expected"
    assert summary["unit"]["command"]=="expected"
    assert summary["unit"]["working_directory"]=="expected"
    assert summary["unit"]["frozen_checkout"]=="present"
    for secret in ("192.168", "private", "aaa", "bbb", "ccc"):
        assert secret not in json.dumps(summary)


def test_last_collection_only_reports_known_bounded_fields(tmp_path,monkeypatch):
    obj, state, policy, service, timer=fixture(tmp_path,monkeypatch)
    last={"schema":1,"status":"observed","performed":True,
          "physical_acceptance":"UNKNOWN","sessions_used":3,
          "sessions_remaining":37, "report_saved_private":True,
          "no_pi_changes_requested":True}
    f=state/"last-status.json"; f.write_text(json.dumps(last)); f.chmod(0o600)
    assert obj.last_summary(state)=={
        "status":"observed", "attempted":True,
        "private_report_saved":True,"sessions_used_at_attempt":3}
    last["host"]="SECRET_ADDR"; f.write_text(json.dumps(last))
    assert obj.last_summary(state)=={"status":"unavailable"}
    f.write_text(json.dumps({"status":"observed"})); f.chmod(0o644)
    assert obj.last_summary(state)=={"status":"unavailable"}


def test_private_policy_symlink_refused_and_no_data_leaks(tmp_path, monkeypatch):
    obj, state, policy, service, timer=fixture(tmp_path,monkeypatch)
    target=state/"recurring-approval.json"
    target.unlink()
    target.symlink_to(tmp_path/"outside")
    assert obj.approved_summary(state,100+3600)=={"status":"unavailable"}
    target.unlink()
    target.write_text('{"enabled":true,"enabled":true}')
    target.chmod(0o600)
    assert obj.approved_summary(state,100+3600)=={"status":"unavailable"}
    target.write_text(json.dumps(policy))
    target.chmod(0o600)
    state.chmod(0o755)
    assert obj.approved_summary(state,100+3600)=={"status":"unavailable"}


def test_expired_exhausted_or_revoked_approval_is_not_falsely_active(tmp_path, monkeypatch):
    obj, state, policy, service, timer=fixture(tmp_path,monkeypatch)
    path=state/"recurring-approval.json"
    def update(**change):
        data=dict(policy,**change)
        path.write_text(json.dumps(data))
        path.chmod(0o600)
    update(enabled=False)
    assert obj.approved_summary(state,100+3600)["status"]=="revoked"
    update(used=40)
    assert obj.approved_summary(state,100+3600)["status"]=="exhausted"
    update()
    assert obj.approved_summary(state,100+14*86400)["status"]=="expired"
    update(max_sessions=41)
    assert obj.approved_summary(state,100+3600)["status"]=="unavailable"


def test_unknown_service_and_timer_do_not_imply_success(tmp_path,monkeypatch):
    obj, state, policy, service, timer=fixture(tmp_path,monkeypatch)
    service.write_text("[Service]\nExecStart=/bin/sh -c 'untrusted'\n")
    timer.write_text("[Timer]\nOnCalendar=*-*-* *:22:00\n")
    answer=obj.inspect(state=state,service=service,timer=timer,now=100+3600,
                       runner=systemd)
    assert answer["unit"]["command"]=="unrecognized"
    assert answer["unit"]["working_directory"]=="unrecognized"
    assert answer["unit"]["hourly_timer"]=="unrecognized"


def test_systemd_status_is_bounded_and_never_echoes_suspicious_text():
    obj=module()
    def malformed(argv,**kwargs):
        return SimpleNamespace(returncode=0,stdout="Result=success\n"
                              "ExecMainStatus=0\nActiveState=inactive\n"
                              "ExecMainCode=1\nSSID=PRIVATE_NETWORK\n")
    assert obj.service_metadata(runner=malformed)["last_result"]=="unavailable"
    def goodfailure(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout=(
            "Result=exit-code\nExecMainStatus=3\n"
            "ActiveState=failed\nExecMainCode=1\n"))
    result=obj.service_metadata(runner=goodfailure)
    assert result["last_result"]=="exit-code"
    assert result["exit_code"]==3
    assert result["active_state"]=="failed"


def test_cli_requires_json_explicit_opt_in(capsys):
    obj=module()
    with pytest.raises(SystemExit) as e:
        obj.main([])
    assert e.value.code==2
