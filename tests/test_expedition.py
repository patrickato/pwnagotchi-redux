"""Expedition session + Wrapped recap."""
from redux.expedition import ExpeditionLog, wrapped
from redux.geo import SightingStore, Sighting


def test_start_end_and_one_at_a_time():
    log = ExpeditionLog()
    a = log.start("morning walk", now=100.0)
    assert log.current().name == "morning walk" and a.active
    # starting another auto-closes the first
    log.start("afternoon", now=200.0)
    closed = [e for e in log.expeditions if e.name == "morning walk"][0]
    assert closed.ended_at == 200.0 and not closed.active
    ended = log.end(now=300.0)
    assert ended.name == "afternoon" and ended.ended_at == 300.0
    assert log.current() is None


def test_persistence_roundtrip(tmp_path):
    p = str(tmp_path / "exp.json")
    log = ExpeditionLog(path=p)
    log.start("field day", now=100.0)
    log.end(now=160.0)
    log.save()
    again = ExpeditionLog.load(p)
    assert len(again.expeditions) == 1
    assert again.expeditions[0].name == "field day" and again.expeditions[0].duration() == 60.0


def test_wrapped_summarizes_only_the_window():
    store = SightingStore(":memory:")
    # before the session (excluded)
    store.insert(Sighting(kind="wifi", mac="aa:bb:cc:00:00:01", ssid="Old", ts=50.0, first_seen=50.0, provenance="t"))
    # during the session
    store.insert(Sighting(kind="wifi", mac="aa:bb:cc:00:00:02", ssid="A", ts=120.0, first_seen=120.0,
                          lat=40.0, lon=-80.0, provenance="t"))
    store.insert(Sighting(kind="ble", mac="11:22:33:44:55:66", ssid="", ts=130.0, first_seen=130.0, provenance="t"))
    from redux.expedition import Expedition
    e = Expedition("walk", started_at=100.0, ended_at=200.0)
    w = wrapped(store, e)
    assert w["total"] == 2                       # the pre-session one is excluded
    assert w["by_kind"] == {"wifi": 1, "ble": 1}
    assert w["discoveries"] == 2                 # both first-seen in-window
    assert w["located"] == 1
    assert "walk" in w["headline"] and "2 sightings" in w["headline"]


def test_wrapped_active_session_uses_now():
    store = SightingStore(":memory:")
    store.insert(Sighting(kind="wifi", mac="aa:bb:cc:00:00:03", ssid="Live", ts=150.0, first_seen=150.0, provenance="t"))
    from redux.expedition import Expedition
    e = Expedition("live", started_at=100.0, ended_at=None)
    w = wrapped(store, e, now=200.0)
    assert w["active"] is True and w["duration_s"] == 100.0 and w["total"] == 1
