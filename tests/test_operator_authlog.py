"""Operator -> signed auth-log integration: the autonomous kill-chain leaves the
same tamper-evident trail a manual run does, and never logs an executor result
(which may carry a recovered secret)."""
import json

from redux.operator import Operator, Phase
from redux.core import Scope

ARMED = "00:11:22:33:44:55"
UNARMED = "aa:bb:cc:dd:ee:ff"


def _op(scope):
    return Operator(scope, offense_enabled=True, caps={"capture.handshake": True})


def _execs():
    return {
        Phase.CAPTURE: lambda t: (True, "handshake captured"),
        Phase.CRACK: lambda t: (True, "PSK=SUPERSECRET123"),      # a secret in the result
        Phase.PIVOT_SCAN: lambda t: (True, "5 hosts"),
        Phase.PIVOT_CRED: lambda t: (True, "admin:hunter2pw"),    # a secret in the result
        Phase.LOOT: lambda t: (True, "looted"),
    }


def test_run_records_each_step_without_leaking_results():
    s = Scope(); s.add(ARMED, "bssid", job="op")
    logged = []
    _op(s).run([ARMED], _execs(), recon=lambda: (True, "12 APs"),
               logger=lambda e, d: logged.append((e, d)))
    events = [e for e, _ in logged]
    assert "campaign.handshake_capture" in events and "campaign.offline_crack" in events
    blob = json.dumps(logged)
    assert "SUPERSECRET123" not in blob and "hunter2pw" not in blob   # results never logged
    for _, d in logged:
        assert set(d) == {"target", "phase", "allowed", "authorized", "executed"}


def test_blocked_step_is_logged_as_not_allowed():
    s = Scope(); s.add(ARMED, "bssid", job="op")
    logged = []
    _op(s).run([UNARMED], _execs(), logger=lambda e, d: logged.append((e, d)))
    cap = [d for e, d in logged if e == "campaign.handshake_capture" and d["target"] == UNARMED]
    assert cap and cap[0]["allowed"] is False and cap[0]["executed"] is False


def test_default_logger_writes_to_the_device_auth_log(tmp_path, monkeypatch):
    from redux.core.authlog import AuthLog
    authlog = tmp_path / "a.jsonl"
    monkeypatch.setenv("REDUX_AUTHLOG", str(authlog))
    monkeypatch.delenv("REDUX_AUTHLOG_KEY", raising=False)
    s = Scope(); s.add(ARMED, "bssid", job="op")
    out = _op(s).run([ARMED], _execs(), recon=lambda: (True, "12 APs"))   # no logger -> default
    assert out["summary"]["executed_steps"] >= 1
    entries = AuthLog(str(authlog)).entries()
    assert entries and any(e.event.startswith("campaign.") for e in entries)
    assert AuthLog(str(authlog)).verify().ok                             # chain intact
