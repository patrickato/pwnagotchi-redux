"""Explain-as-it-goes — auth log annotated with blue-team answers."""
from types import SimpleNamespace

from redux.intel.narrate import narrate, NarratedLine


def _e(ts, event, detail=None):
    return SimpleNamespace(ts=ts, event=event, detail=detail or {})


def test_offensive_events_get_blue_answers():
    lines = narrate([_e(1.0, "campaign.deauth", {"target": "aa:bb"}),
                     _e(2.0, "campaign.handshake_capture", {"target": "aa:bb"})])
    deauth = lines[0]
    assert deauth.technique and ("PMF" in deauth.mitigation or "802.11w" in deauth.mitigation)
    assert lines[1].technique and "WPA3" in lines[1].mitigation or lines[1].detection


def test_non_offensive_events_have_no_technique():
    lines = narrate([_e(1.0, "scope.add", {"value": "aa:bb"}),
                     _e(2.0, "scope.lab_mode", {"state": "on"})])
    assert all(l.technique is None for l in lines)
    assert lines[0].event == "scope.add"


def test_prefix_is_stripped_to_find_the_action():
    # both "campaign.deauth" and bare "deauth" resolve to the same technique
    a = narrate([_e(1.0, "campaign.deauth", {})])[0]
    b = narrate([_e(1.0, "fire.deauth", {})])[0]
    assert a.technique == b.technique and a.technique is not None


def test_empty():
    assert narrate([]) == []


# --- CLI -------------------------------------------------------------------- #

def test_cmd_narrate_human_and_json(tmp_path, capsys):
    from redux.cli import cmd_narrate
    from redux.core.authlog import AuthLog
    import json as _json
    log = tmp_path / "a.jsonl"
    al = AuthLog(str(log))
    al.record("scope.add", {"value": "aa:bb:cc:dd:ee:ff"})
    al.record("campaign.deauth", {"target": "aa:bb:cc:dd:ee:ff"})
    assert cmd_narrate(SimpleNamespace(authlog=str(log), json=False)) == 0
    out = capsys.readouterr().out
    assert "NARRATE" in out and "blue sees" in out and "blue stops" in out
    assert cmd_narrate(SimpleNamespace(authlog=str(log), json=True)) == 0
    payload = _json.loads(capsys.readouterr().out)
    deauth = [p for p in payload if p["event"] == "campaign.deauth"][0]
    assert deauth["technique"] and deauth["mitigation"]
    scope = [p for p in payload if p["event"] == "scope.add"][0]
    assert scope["technique"] is None
