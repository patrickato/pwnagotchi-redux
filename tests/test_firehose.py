"""Glass-box firehose — merged decision+sighting stream, LAB-only (engine + CLI)."""
from types import SimpleNamespace

from redux.intel.firehose import firehose, FirehoseLine


def _ent(seq, ts, event, detail):
    return SimpleNamespace(seq=seq, ts=ts, event=event, detail=detail)


def _sight(mac, ts, kind="wifi", ssid="", rssi=None, channel=None):
    return SimpleNamespace(mac=mac, ts=ts, kind=kind, ssid=ssid, rssi=rssi, channel=channel)


def test_merges_and_orders_by_time():
    entries = [_ent(0, 100.0, "scope.add", {"value": "aa:bb"}),
               _ent(1, 300.0, "fire.deauth", {"bssid": "aa:bb"})]
    sights = [_sight("cc:dd", 200.0, ssid="Net", rssi=-40, channel=6)]
    lines = firehose(entries, sights)
    assert [l.ts for l in lines] == [100.0, 200.0, 300.0]       # interleaved by time
    assert lines[0].source == "authlog" and lines[1].source == "wifi"
    assert "scope.add" in lines[0].text and "Net" in lines[1].text


def test_limit_keeps_most_recent():
    entries = [_ent(i, float(i), "e", {}) for i in range(10)]
    lines = firehose(entries, [], limit=3)
    assert [l.ts for l in lines] == [7.0, 8.0, 9.0]


def test_empty_is_empty():
    assert firehose([], []) == []


# --- CLI (LAB-only gate) ---------------------------------------------------- #

def test_cmd_firehose_refused_in_field(tmp_path, capsys):
    from redux.cli import cmd_firehose
    from redux.core.scope import Scope
    sf = tmp_path / "scope.json"
    Scope(path=str(sf)).save()                              # FIELD (lab off)
    rc = cmd_firehose(SimpleNamespace(scope_file=str(sf), authlog=None, db=None, tail=200, json=False))
    assert rc == 2 and "LAB-only" in capsys.readouterr().out


def test_cmd_firehose_runs_in_lab(tmp_path, capsys):
    from redux.cli import cmd_firehose
    from redux.core.scope import Scope
    from redux.core.authlog import AuthLog
    sf = tmp_path / "scope.json"
    Scope(path=str(sf), lab_mode=True).save()
    log = tmp_path / "a.jsonl"
    AuthLog(str(log)).record("scope.add", {"value": "aa:bb:cc:dd:ee:ff"})
    rc = cmd_firehose(SimpleNamespace(scope_file=str(sf), authlog=str(log),
                                      db=str(tmp_path / "none.db"), tail=200, json=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "GLASS-BOX FIREHOSE [LAB]" in out and "scope.add" in out
