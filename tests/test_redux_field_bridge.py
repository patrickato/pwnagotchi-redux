"""Offline contract tests: NEVER SSH the Pi or touch real capture evidence."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "redux-field-bridge.py"


def bridge():
    spec = importlib.util.spec_from_file_location("redux_field_bridge", SCRIPT)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def setup_private(tmp_path, monkeypatch):
    module = bridge()
    monkeypatch.setattr(module, "STATE", tmp_path / "field-private")
    key = tmp_path / "field.key"
    key.write_text("not a real key")
    key.chmod(0o600)
    return module, key


def test_plan_is_strictly_offline(tmp_path, monkeypatch, capsys):
    mod, key = setup_private(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "invoke_collector",
                        lambda *args, **kwargs: pytest.fail("hardware invoked"))
    assert mod.main(["plan"]) == 0
    scope = json.loads(capsys.readouterr().out)
    assert scope["no_sudo"] and scope["no_rf_changes"] and scope["no_pi_writes"]
    assert scope["no_packet_sockets"] and scope["no_pcap_reads"]
    assert scope["default_network_action"] is False
    assert scope["physical_acceptance"] == "UNKNOWN"
    assert not mod.STATE.exists()


def test_approval_required_and_private_policy(tmp_path, monkeypatch):
    mod, key = setup_private(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="explicit_recurring_approval_required"):
        mod.enroll("192.168.56.24", key, interval_hours=6, days=14,
                   sessions=30, approve=False, now=100)
    assert not mod.STATE.exists()
    value = mod.enroll("192.168.56.24", key, interval_hours=6, days=14,
                       sessions=30, approve=True, now=100)
    assert value["hardware_action_performed"] is False
    policy = mod.STATE / mod.POLICY_FILE
    assert stat.S_IMODE(policy.stat().st_mode) == 0o600
    assert stat.S_IMODE(mod.STATE.stat().st_mode) == 0o700
    assert mod.load_policy()["max_sessions"] == 30
    assert "192.168.56.24" not in json.dumps(value)


@pytest.mark.parametrize("host", ["127.0.0.1", "8.8.8.8", "0.0.0.0",
                                  "224.0.0.1", "::1", "169.254.1.2"])
def test_only_private_lan_ipv4_allowed(tmp_path, monkeypatch, host):
    mod, key = setup_private(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        mod.enroll(host, key, interval_hours=6, days=1, sessions=3,
                   approve=True, now=100)


@pytest.mark.parametrize("hours,days,count", [(3, 14, 20), (6, 15, 20),
                                               (6, 14, 85), (6, 0, 2)])
def test_recurring_scope_limits_are_enforced(tmp_path, monkeypatch, hours, days, count):
    mod, key = setup_private(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        mod.enroll("192.168.56.24", key, interval_hours=hours,
                   days=days, sessions=count, approve=True, now=100)


def test_observe_consumes_approval_before_single_ssh(tmp_path, monkeypatch):
    mod, key = setup_private(tmp_path, monkeypatch)
    mod.enroll("192.168.56.24", key, interval_hours=4, days=3,
               sessions=2, approve=True, now=100)
    calls = []

    def fake_runner(command, **kwargs):
        calls.append(command)
        assert "--authorize-read-only" in command
        assert "--card-role" in command
        assert command[command.index("--card-role") + 1] == "unverified"
        assert "sudo" not in command
        assert kwargs["timeout"] == 75
        path = Path(command[command.index("--output") + 1])
        path.write_text('{"format":"fake"}')
        path.chmod(0o600)
        return SimpleNamespace(returncode=3, stdout=json.dumps({
            "session_report_saved": True,
            "physical_acceptance": "UNKNOWN",
            "network_actions_performed": True,
        }), stderr="")

    one = mod.run_once(now=110, runner=fake_runner)
    assert one["status"] == "observed" and one["sessions_remaining"] == 1
    assert len(calls) == 1
    assert mod.load_policy()["used"] == 1
    two = mod.run_once(now=111, runner=fake_runner)
    assert two["status"] == "rate_limited" and len(calls) == 1
    three = mod.run_once(now=110 + 4*3600, runner=fake_runner)
    assert three["status"] == "observed" and len(calls) == 2
    four = mod.run_once(now=110 + 8*3600, runner=fake_runner)
    assert four["status"] == "exhausted" and len(calls) == 2
    assert "192.168" not in json.dumps(one)
    assert stat.S_IMODE((mod.STATE / "last-status.json").stat().st_mode) == 0o600


def test_recurring_permission_expires_and_can_be_revoked(tmp_path, monkeypatch):
    mod, key = setup_private(tmp_path, monkeypatch)
    mod.enroll("192.168.56.24", key, interval_hours=4, days=1,
               sessions=10, approve=True, now=100)
    runner = lambda *args, **kwargs: pytest.fail("forbidden collector attempt")
    assert mod.run_once(now=100 + 86400, runner=runner)["status"] == "expired"
    assert mod.revoke()["status"] == "revoked"
    assert mod.run_once(now=101, runner=runner)["status"] == "revoked"


def test_unavailable_session_does_not_retry(tmp_path, monkeypatch):
    mod, key = setup_private(tmp_path, monkeypatch)
    mod.enroll("192.168.56.24", key, interval_hours=4, days=1,
               sessions=10, approve=True, now=100)
    calls = []

    def failed(*args, **kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired(["collector"], timeout=75)

    result = mod.run_once(now=102, runner=failed)
    assert result["status"] == "unavailable"
    assert mod.run_once(now=105, runner=failed)["status"] == "rate_limited"
    assert len(calls) == 1
    assert mod.load_policy()["used"] == 1


def test_policy_path_is_private_and_cannot_be_symlink(tmp_path, monkeypatch):
    mod, key = setup_private(tmp_path, monkeypatch)
    mod.enroll("192.168.56.24", key, interval_hours=4, days=1,
               sessions=5, approve=True, now=100)
    policy = mod.STATE / mod.POLICY_FILE
    policy.unlink()
    policy.symlink_to(key)
    with pytest.raises(ValueError, match="untrusted_local_policy"):
        mod.load_policy()


def test_observe_not_enrolled_does_not_connect(tmp_path, monkeypatch):
    mod, key = setup_private(tmp_path, monkeypatch)
    assert mod.run_once(now=123, runner=lambda *a, **kw:
                        pytest.fail("connected without enrollment"))["status"] == "not_enrolled"


def test_plan_cli_no_permissions_needed(tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "plan"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert json.loads(result.stdout)["default_network_action"] is False


def test_collector_source_change_blocks_without_ssh(tmp_path, monkeypatch):
    mod, key = setup_private(tmp_path, monkeypatch)
    approved = ("1" * 64, "2" * 64)
    monkeypatch.setattr(mod, "source_hashes", lambda: approved)
    mod.enroll("192.168.56.24", key, interval_hours=4, days=3,
               sessions=3, approve=True, now=100)
    assert mod.load_policy()["remote_bundle_sha256"] == "2" * 64
    monkeypatch.setattr(mod, "source_hashes",
                        lambda: ("1" * 64, "3" * 64))
    verdict = mod.run_once(now=105, runner=lambda *args, **kwargs:
                           pytest.fail("collector changed without approval"))
    assert verdict["status"] == "source_changed"
    assert mod.load_policy()["used"] == 0


def test_symlinked_local_lock_refuses_session(tmp_path, monkeypatch):
    mod, key = setup_private(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "source_hashes", lambda: ("1" * 64, "2" * 64))
    mod.enroll("192.168.56.24", key, interval_hours=4, days=3,
               sessions=3, approve=True, now=100)
    (mod.STATE / "collector.lock").symlink_to(key)
    with pytest.raises(OSError):
        mod.run_once(now=105, runner=lambda *a, **kw:
                     pytest.fail("unsafe lock should stop first"))


def test_approval_has_independent_hash_pins(tmp_path, monkeypatch):
    mod, key = setup_private(tmp_path, monkeypatch)
    values = ("a" * 64, "b" * 64)
    monkeypatch.setattr(mod, "source_hashes", lambda: values)
    mod.enroll("192.168.56.24", key, interval_hours=6, days=14,
               sessions=10, approve=True, now=100)
    pol = mod.load_policy()
    assert pol["launcher_sha256"] == values[0]
    assert pol["remote_bundle_sha256"] == values[1]
    assert "a" * 64 not in json.dumps(mod.plan())


def test_field_timer_installer_is_syntax_valid_and_independent():
    installer = ROOT / "scripts" / "install-redux-field-bridge.sh"
    result = subprocess.run(
        ["bash", "-n", str(installer)],
        cwd=ROOT, text=True, capture_output=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    shell = installer.read_text()
    assert "redux-field-bridge.service" in shell
    assert "--approve-recurring-read-only" not in shell
    assert "sudo" not in shell
    assert "redux-autopilot.service" not in shell


def test_policy_rejects_forged_source_pins(tmp_path, monkeypatch):
    mod, key = setup_private(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "source_hashes",
                        lambda: ("a" * 64, "b" * 64))
    mod.enroll("192.168.56.24", key, interval_hours=6, days=1,
               sessions=2, approve=True, now=100)
    value = mod.load_policy()
    value["remote_bundle_sha256"] = "UNTRUSTED"
    with pytest.raises(ValueError, match="invalid_source_pins"):
        mod.validate_policy(value)


def test_duplicate_json_policy_key_fails_closed(tmp_path, monkeypatch):
    mod, key = setup_private(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "source_hashes", lambda: ("a" * 64, "b" * 64))
    mod.enroll("192.168.56.24", key, interval_hours=4, days=2,
               sessions=6, approve=True, now=100)
    target = mod.STATE / mod.POLICY_FILE
    original = target.read_text()
    target.write_text(original.replace('"enabled": true',
                                       '"enabled": true, "enabled": false'))
    target.chmod(0o600)
    with pytest.raises(ValueError, match="duplicate_policy_field"):
        mod.load_policy()


def test_nonstring_target_or_ssh_key_path_fails_closed(tmp_path, monkeypatch):
    mod, key = setup_private(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="invalid_ip_type"):
        mod.private_ip(3232235521)
    with pytest.raises(ValueError, match="invalid_key_path_type"):
        mod.key_path(1)
