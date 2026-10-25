"""Unleashed autopilot — LAB-only full-chain loop (gate + orchestration)."""
from types import SimpleNamespace

from redux.operator import autopilot, AutopilotReport
from redux.operator.operator import Phase
from redux.core import Scope

ARMED = "00:11:22:33:44:55"


def _execs():
    ok = lambda name: (lambda t: (True, f"{name} ok on {t}"))
    return {Phase.CAPTURE: ok("cap"), Phase.CRACK: ok("crack"),
            Phase.PIVOT_SCAN: ok("scan"), Phase.PIVOT_CRED: ok("cred"), Phase.LOOT: ok("loot")}


def _scope(lab):
    s = Scope(lab_mode=lab)
    s.add(ARMED, "bssid", job="cage")
    return s


# --- the LAB-only gate (the whole point) ------------------------------------ #

def test_field_refuses_and_runs_nothing():
    rep = autopilot(_scope(lab=False), target_source=lambda: [ARMED],
                    executors=_execs(), confirmed=True)       # confirmed, but FIELD
    assert rep.ran is False and rep.mode == "field"
    assert "FIELD" in rep.reason and not rep.cycles


def test_lab_without_confirmation_refuses():
    rep = autopilot(_scope(lab=True), target_source=lambda: [ARMED],
                    executors=_execs(), confirmed=False)
    assert rep.ran is False and "confirm" in rep.reason.lower()


def test_lab_confirmed_runs_the_chain():
    rep = autopilot(_scope(lab=True), target_source=lambda: [ARMED],
                    executors=_execs(), recon=lambda: (True, "seen"), confirmed=True)
    assert rep.ran and rep.mode == "lab" and rep.total_executed >= 1
    assert len(rep.cycles) == 1 and rep.cycles[0].targets == 1


# --- orchestration ---------------------------------------------------------- #

def test_multiple_cycles_and_crack_fn():
    seen = {"n": 0}
    def crack(loot):
        seen["n"] += 1
        return len(loot)
    rep = autopilot(_scope(lab=True), target_source=lambda: [ARMED], executors=_execs(),
                    crack_fn=crack, max_cycles=3, confirmed=True)
    assert len(rep.cycles) == 3 and seen["n"] == 3
    assert rep.total_cracked >= 1          # offline_crack/loot steps were counted


def test_lab_approves_targets_on_sight_in_the_cage():
    # LAB (your closed cage) approves any concrete target on sight — that's the
    # whole point of lab mode; FIELD is where an unarmed target is refused, and
    # unleashed can't run in FIELD at all (see test_field_refuses_and_runs_nothing).
    rep = autopilot(_scope(lab=True), target_source=lambda: ["99:99:99:99:99:99"],
                    executors=_execs(), confirmed=True)       # not pre-armed, but LAB
    assert rep.ran and rep.total_executed >= 1 and rep.total_blocked == 0


def test_empty_target_set_is_a_quiet_cycle():
    rep = autopilot(_scope(lab=True), target_source=lambda: [], executors=_execs(), confirmed=True)
    assert rep.ran and rep.cycles[0].targets == 0 and rep.total_executed == 0


# --- CLI -------------------------------------------------------------------- #

def test_cmd_campaign_unleashed_field_refused(tmp_path, capsys, monkeypatch):
    from redux.cli import cmd_campaign
    monkeypatch.setenv("REDUX_AUTHLOG", str(tmp_path / "a.jsonl"))
    sf = tmp_path / "scope.json"
    s = Scope(path=str(sf)); s.add(ARMED, "bssid", job="cage"); s.save()   # FIELD (lab_mode off)
    rc = cmd_campaign(SimpleNamespace(campaign_cmd="unleashed", persona=None,
                                      scope_file=str(sf), cycles=1, targets=None, confirm=True))
    assert rc == 2 and "not run" in capsys.readouterr().out.lower()


def test_cmd_campaign_unleashed_lab_runs(tmp_path, capsys, monkeypatch):
    from redux.cli import cmd_campaign
    monkeypatch.setenv("REDUX_AUTHLOG", str(tmp_path / "a.jsonl"))
    sf = tmp_path / "scope.json"
    s = Scope(path=str(sf), lab_mode=True); s.add(ARMED, "bssid", job="cage"); s.save()
    rc = cmd_campaign(SimpleNamespace(campaign_cmd="unleashed", persona=None,
                                      scope_file=str(sf), cycles=2, targets=None, confirm=True))
    assert rc == 0
    assert "UNLEASHED [LAB]" in capsys.readouterr().out
