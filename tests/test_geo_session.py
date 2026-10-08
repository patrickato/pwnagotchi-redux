"""Hardware-free tests for survey session (Grok GG8)."""
from redux.geo import Sighting, SightingStore
from redux.geo.session import SurveySession, run_session_into_store


def test_session_stats():
    s = SurveySession(name="walk", started_ts=100.0)
    s.record(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=101.0, provenance="t"))
    s.record(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=102.0, provenance="t"))
    s.stop()
    st = s.stats()
    assert st["observations"] == 2
    assert st["unique_devices"] == 2
    assert st["active"] is False
    assert "session" in st["reason"]


def test_run_into_store():
    sess = SurveySession(name="lab", started_ts=1.0)
    with SightingStore(":memory:") as db:
        st = run_session_into_store(
            sess,
            db,
            [Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=2.0, provenance="t")],
        )
        assert st["observations"] == 1
        assert db.count() == 1
