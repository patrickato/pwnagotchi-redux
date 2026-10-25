"""Live-fire purple range — LAB-only red-vs-blue scorecard."""
from types import SimpleNamespace

from redux.intel.livefire import live_fire, LiveFireReport
from redux.core import Scope


def _scope(lab):
    return Scope(lab_mode=lab)


# --- the LAB-only gate ------------------------------------------------------ #

def test_field_refuses():
    rep = live_fire(_scope(lab=False), executed_actions=["deauth"], confirmed=True)
    assert rep.ran is False and rep.mode == "field" and "FIELD" in rep.reason


def test_lab_without_confirm_refuses():
    rep = live_fire(_scope(lab=True), executed_actions=["deauth"], confirmed=False)
    assert rep.ran is False and "confirm" in rep.reason.lower()


# --- scoring (reuses frameworks.range) -------------------------------------- #

def test_lab_scores_caught_and_missed():
    rep = live_fire(_scope(lab=True),
                    executed_actions=["deauth", "handshake_capture"],
                    fired_by_action={"deauth": ["deauth-flood", "surveillance-sweep"],
                                     "handshake_capture": []},  # blue saw the deauth, missed the capture
                    confirmed=True)
    assert rep.ran and rep.mode == "lab"
    verdicts = {r["action"]: r["verdict"] for r in rep.results}
    assert verdicts["deauth"] == "detected"
    assert verdicts["handshake_capture"] == "missed"
    assert rep.caught == 1 and rep.missed == 1
    assert rep.coverage is not None and rep.coverage.get("gaps")


def test_empty_detections_all_missed():
    rep = live_fire(_scope(lab=True), executed_actions=["deauth"], fired_by_action={}, confirmed=True)
    assert rep.ran and rep.missed == 1 and rep.caught == 0


# --- CLI -------------------------------------------------------------------- #

def test_cmd_range_livefire_field_refused(tmp_path, capsys):
    from redux.cli import cmd_range
    sf = tmp_path / "scope.json"
    s = Scope(path=str(sf)); s.add("00:11:22:33:44:55", "bssid", job="cage"); s.save()  # FIELD
    rc = cmd_range(SimpleNamespace(range_cmd="livefire", scope_file=str(sf),
                                   targets=None, fired=None, confirm=True))
    assert rc == 2 and "not run" in capsys.readouterr().out.lower()


def test_cmd_range_livefire_lab_runs(tmp_path, capsys):
    from redux.cli import cmd_range
    sf = tmp_path / "scope.json"
    s = Scope(path=str(sf), lab_mode=True); s.add("00:11:22:33:44:55", "bssid", job="cage"); s.save()
    rc = cmd_range(SimpleNamespace(range_cmd="livefire", scope_file=str(sf), targets=None,
                                   fired=["deauth-flood", "surveillance-sweep"], confirm=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "LIVE-FIRE [LAB]" in out and "blue coverage" in out
