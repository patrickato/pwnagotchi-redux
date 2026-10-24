"""LAB <-> FIELD operating-mode switch (what the TFT switch calls)."""
from redux.core.mode import read_mode, set_mode, toggle_mode, LAB, FIELD
from redux.core.scope import Scope

OK = lambda: True   # a restart that succeeds, injected so tests touch no systemd


def test_read_mode_defaults_to_field(tmp_path):
    assert read_mode(str(tmp_path / "nope.json")) == FIELD


def test_set_lab_persists_and_restarts(tmp_path):
    p = str(tmp_path / "scope.json")
    res = set_mode(LAB, p, restart=OK)
    assert res["ok"] and res["mode"] == LAB and res["restarted"]
    assert read_mode(p) == LAB
    assert Scope.load(p).lab_mode is True


def test_field_keeps_armed_targets(tmp_path):
    p = str(tmp_path / "scope.json")
    s = Scope(lab_mode=True)
    s.add("aa:bb:cc:dd:ee:ff")
    s.save(p)
    assert set_mode(FIELD, p, restart=OK)["ok"]
    reloaded = Scope.load(p)
    assert reloaded.lab_mode is False                 # locked down
    assert len(reloaded.active_entries()) == 1        # but armed target preserved


def test_toggle_flips_both_ways(tmp_path):
    p = str(tmp_path / "scope.json")
    set_mode(FIELD, p, restart=OK)
    assert toggle_mode(p, restart=OK)["mode"] == LAB
    assert toggle_mode(p, restart=OK)["mode"] == FIELD


def test_unknown_mode_refused(tmp_path):
    res = set_mode("banana", str(tmp_path / "s.json"), restart=OK)
    assert not res["ok"] and "unknown mode" in res["reason"]


def test_restart_failure_is_reported_not_hidden(tmp_path):
    res = set_mode(LAB, str(tmp_path / "s.json"), restart=lambda: False)
    assert res["ok"] and res["restarted"] is False
    assert "restart redux-live.service" in res["reason"]
