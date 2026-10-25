"""Crack ROI triage + passive-capture forecast (engine + CLI).

hc22000 line layout (see redux.crack.ingest.valid_22000_line):
  type 01 = PMKID:     WPA*01*<32hex>*<ap12>*<cl12>*<essid_hex>***
  type 02 = handshake: WPA*02*<32hex>*<ap12>*<cl12>*<essid_hex>*<64hex>*<eapol>*<2hex>
"""
from redux.crack.triage import (
    ssid_crack_prior, parse_22000_targets, triage_crack_queue,
    capture_forecast, triage_capture, CrackTarget,
)


def _pmkid(ssid, ap="001122334455"):
    return f"WPA*01*{'a' * 32}*{ap}*{'bb' * 6}*{ssid.encode().hex()}***"


def _handshake(ssid, ap="001122334455"):
    return f"WPA*02*{'a' * 32}*{ap}*{'bb' * 6}*{ssid.encode().hex()}*{'c' * 64}*{'dd' * 8}*00"


# --- parsing ---------------------------------------------------------------- #

def test_parse_extracts_ssid_bssid_and_artifact_type():
    text = "\n".join([_pmkid("NETGEAR58", "aabbccddeeff"),
                       _handshake("MyHome", "001122334455"),
                       "garbage line that is not 22000"])
    got = parse_22000_targets(text)
    assert ("NETGEAR58", "aa:bb:cc:dd:ee:ff", "pmkid") in got
    assert ("MyHome", "00:11:22:33:44:55", "handshake") in got
    assert len(got) == 2                         # the junk line is dropped


# --- SSID prior ------------------------------------------------------------- #

def test_ssid_prior_buckets():
    assert ssid_crack_prior("NETGEAR58")[0] == 0.40      # vendor/default
    assert ssid_crack_prior("12345678")[0] == 0.30       # all-numeric
    assert ssid_crack_prior("Starbucks Guest")[0] == 0.25  # guest/shared
    assert ssid_crack_prior("Patricks Place")[0] == 0.15  # custom
    # each carries a glass-box rationale and a wordlist hint
    prob, hint, why = ssid_crack_prior("Orbi")
    assert hint and why and 0 < prob <= 1


# --- crack ROI ranking ------------------------------------------------------ #

def test_triage_ranks_by_roi_and_dedupes_by_bssid():
    targets = [
        ("Linksys00", "00:00:00:00:00:01", "handshake"),   # vendor -> 40
        ("87654321",  "00:00:00:00:00:02", "pmkid"),        # numeric -> 30
        ("Grandmas iPad Net", "00:00:00:00:00:03", "pmkid"),  # custom -> 15
    ]
    rows = triage_crack_queue(targets)
    assert [r.bssid[-1] for r in rows] == ["1", "2", "3"]   # 40 > 30 > 15
    assert rows[0].tier == "high" and rows[1].tier == "medium" and rows[2].tier == "low"


def test_holding_both_artifacts_is_one_row_with_a_small_bump():
    rows = triage_crack_queue([
        ("NETGEAR58", "aa:bb:cc:dd:ee:ff", "pmkid"),
        ("NETGEAR58", "aa:bb:cc:dd:ee:ff", "handshake"),
    ])
    assert len(rows) == 1
    assert rows[0].artifacts == ("handshake", "pmkid")
    assert rows[0].priority == 42.0                         # 40 * 1.05


def test_hidden_ssid_still_triaged():
    rows = triage_crack_queue([("", "00:00:00:00:00:09", "pmkid")])
    assert len(rows) == 1 and rows[0].ssid == ""
    assert rows[0].prob == 0.15


# --- passive-capture forecast ----------------------------------------------- #

def test_forecast_no_clients_recommends_pmkid():
    f = capture_forecast("GhostNet", 0)
    assert f.mean_hours is None and "PMKID" in f.recommend


def test_forecast_many_clients_is_soon_and_waits():
    f = capture_forecast("BusyCafe", 4, reassoc_per_hour=0.5)   # rate 2/h -> mean 0.5h
    assert f.mean_hours == 0.5 and "wait" in f.recommend.lower()


def test_forecast_one_client_is_slow_and_suggests_deauth():
    f = capture_forecast("QuietAP", 1, reassoc_per_hour=0.5)    # rate 0.5/h -> mean 2h
    assert f.mean_hours == 2.0 and "deauth" in f.recommend.lower()


def test_triage_capture_sorts_by_client_count():
    rows = [{"ssid": "A", "client_count": 1}, {"ssid": "B", "client_count": 5},
            {"ssid": "", "client_count": 9}]        # blank SSID dropped
    out = triage_capture(rows)
    assert [c.ssid for c in out] == ["B", "A"]


# --- CLI -------------------------------------------------------------------- #

def test_cmd_triage_human_and_json(tmp_path, capsys):
    from redux.cli import cmd_triage
    from redux.portal.karma import ProbeHarvester
    from types import SimpleNamespace
    import json as _json
    # a crack queue
    ready = tmp_path / "ready"
    ready.mkdir()
    (ready / "a.hc22000").write_text(_pmkid("NETGEAR58", "aabbccddeeff") + "\n")
    (ready / "b.hc22000").write_text(_handshake("Custom Home", "001122334455") + "\n")
    # a probe store
    pnl = tmp_path / "pnl.json"
    h = ProbeHarvester(path=str(pnl))
    h.note("fa:11:22:33:44:01", "CoffeeShop")
    h.note("fe:22:33:44:55:02", "CoffeeShop")
    h.note("aa:33:44:55:66:03", "HomeNet")
    base = dict(dir=str(ready), file=None, pnl=str(pnl), reassoc_per_hour=0.5)
    assert cmd_triage(SimpleNamespace(json=False, **base)) == 0
    out = capsys.readouterr().out
    assert "CRACK ROI" in out and "NETGEAR58" in out
    assert "CAPTURE FORECAST" in out and "CoffeeShop" in out
    # JSON form
    assert cmd_triage(SimpleNamespace(json=True, **base)) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert len(payload["crack_queue"]) == 2
    assert payload["crack_queue"][0]["ssid"] == "NETGEAR58"   # vendor ranked first
    assert any(c["ssid"] == "CoffeeShop" and c["clients"] == 2
               for c in payload["capture_forecast"])


def test_cmd_triage_empty_is_honest(tmp_path, capsys):
    from redux.cli import cmd_triage
    from types import SimpleNamespace
    assert cmd_triage(SimpleNamespace(dir=str(tmp_path / "nope"), file=None,
                                      pnl=str(tmp_path / "none.json"),
                                      reassoc_per_hour=0.5, json=False)) == 0
    out = capsys.readouterr().out
    assert "no .hc22000 artifacts found" in out and "no harvested probes yet" in out
