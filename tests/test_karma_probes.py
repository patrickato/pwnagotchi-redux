"""Probe harvesting (passive) + AIMED karma planning (scope-gated, no spam)."""
from redux.portal.karma import ProbeHarvester, karma_plan, MAX_ADVERTISE
from redux.core.scope import Scope


class _Ev:
    def __init__(self, type, data): self.type = type; self.data = data


class _Em:
    def __init__(self, event): self.payload = {"event": event}


# ---- harvester ----------------------------------------------------------- #

def test_note_builds_pnl_and_ignores_empty():
    h = ProbeHarvester(now=lambda: 10.0)
    assert h.note("AA:BB:CC:00:00:01", "CorpWiFi")
    assert h.note("aa:bb:cc:00:00:01", "home-net")
    assert not h.note("aa:bb:cc:00:00:01", "")      # wildcard/broadcast probe ignored
    assert not h.note("", "x")
    assert h.pnl("aa:bb:cc:00:00:01") == ["CorpWiFi", "home-net"]


def test_observe_harvests_from_probe_events():
    h = ProbeHarvester()
    h.observe(_Em(_Ev("client.probe", {"from": "aa:bb:cc:00:00:02", "essid": "Starbucks"})))
    h.observe(_Em(_Ev("client.new", {"mac": "aa:bb:cc:00:00:03"})))   # not a probe -> ignored
    assert h.pnl("aa:bb:cc:00:00:02") == ["Starbucks"]
    assert h.clients()[0]["client"] == "aa:bb:cc:00:00:02"


def test_ssids_ranked_by_client_popularity():
    h = ProbeHarvester()
    for c in ("11:11:11:11:11:11", "22:22:22:22:22:22"):
        h.note(c, "Popular")
    h.note("33:33:33:33:33:33", "Rare")
    ss = h.ssids()
    assert ss[0]["ssid"] == "Popular" and ss[0]["client_count"] == 2
    assert ss[1]["ssid"] == "Rare" and ss[1]["client_count"] == 1


def test_persistence_roundtrip(tmp_path):
    p = tmp_path / "pnl.json"
    h = ProbeHarvester(path=p)
    h.observe(_Em(_Ev("client.probe", {"from": "aa:bb:cc:00:00:04", "essid": "Net"})))
    h2 = ProbeHarvester(path=p)                      # reload from disk
    assert h2.pnl("aa:bb:cc:00:00:04") == ["Net"]


# ---- aimed karma plan ---------------------------------------------------- #

def _harv(spec):
    h = ProbeHarvester()
    for client, ssid in spec:
        h.note(client, ssid)
    return h


def test_plan_only_advertises_in_scope_clients():
    mine = "aa:bb:cc:00:00:01"
    stranger = "de:ad:be:ef:00:99"
    h = _harv([(mine, "MyLab"), (stranger, "SomeoneElse")])
    scope = Scope()
    scope.add(mine, job="lab")                       # only my client is in scope
    plan = karma_plan(h, scope, lab_mode=False)
    assert "MyLab" in plan.ssid_list
    assert "SomeoneElse" not in plan.ssid_list       # bystander SSID refused
    assert any("SomeoneElse" in s["reason"] for s in plan.skipped)


def test_plan_refuses_bystander_only_ssids_entirely():
    h = _harv([("de:ad:be:ef:00:01", "Neighbor")])
    plan = karma_plan(h, Scope(), lab_mode=False)    # empty scope, field
    assert plan.advertise == () and not plan.armed
    assert "nothing to advertise" in plan.reason


def test_lab_mode_approves_on_sight_and_arms():
    h = _harv([("aa:bb:cc:00:00:01", "LabNet")])
    plan = karma_plan(h, Scope(lab_mode=True))       # lab -> every client in scope
    assert plan.armed and plan.ssid_list == ["LabNet"]
    assert "LAB" in plan.reason


def test_field_with_scope_is_advisory_not_armed():
    mine = "aa:bb:cc:00:00:01"
    h = _harv([(mine, "MyLab")])
    scope = Scope(); scope.add(mine, job="lab")
    plan = karma_plan(h, scope, lab_mode=False)
    assert plan.advertise and not plan.armed         # planned, but not firing in field
    assert "advisory" in plan.reason


def test_plan_is_capped_no_blasting():
    # 50 distinct in-scope clients each wanting a unique SSID -> capped
    h = ProbeHarvester()
    for i in range(50):
        h.note(f"aa:bb:cc:00:00:{i:02x}", f"Net{i}")
    plan = karma_plan(h, Scope(lab_mode=True))
    assert len(plan.advertise) == MAX_ADVERTISE      # never the whole room
