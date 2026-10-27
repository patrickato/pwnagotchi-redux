"""Engagement case file — durable open/close tying Scope+authlog+loot+notebook+report."""
from types import SimpleNamespace

import pytest

from redux.core.engagement import EngagementStore, Engagement
from redux.core.scope import Scope


def _store(tmp_path):
    return EngagementStore(dir=str(tmp_path / "engagements"))


def _scope():
    s = Scope()
    s.add("aa:bb:cc:dd:ee:ff", "bssid", job="lab")
    return s


def _entry(seq, event, **detail):
    return SimpleNamespace(seq=seq, ts=100.0 + seq, event=event, detail=detail)


def test_open_snapshots_authorization(tmp_path):
    st = _store(tmp_path)
    eng = st.open("Acme Q1 pentest", operator="pat", authorization="SOW-2026-14",
                  scope=_scope(), now=1000.0)
    assert eng.status == "active" and eng.id.startswith("acme-q1-pentest-")
    assert eng.authorization == "SOW-2026-14"
    assert len(eng.scope_opened) == 1 and eng.scope_opened[0]["kind"] == "bssid"
    # persisted + retrievable
    assert st.get(eng.id).name == "Acme Q1 pentest"
    assert st.active().id == eng.id


def test_one_active_engagement_at_a_time(tmp_path):
    st = _store(tmp_path)
    st.open("first", scope=_scope(), now=1.0)
    with pytest.raises(ValueError):
        st.open("second", scope=_scope(), now=2.0)
    # force opens a parallel one
    assert st.open("second", scope=_scope(), now=3.0, force=True).name == "second"


def test_close_pulls_authlog_loot_notebook_into_report(tmp_path):
    st = _store(tmp_path)
    scope = _scope()
    eng = st.open("lab run", scope=scope, now=1000.0)
    entries = [
        _entry(1, "handshake_capture", bssid="aa:bb:cc:dd:ee:ff"),
        _entry(2, "deauth", bssid="aa:bb:cc:dd:ee:ff"),
    ]
    closed = st.close(eng.id, scope=scope, authlog_entries=entries,
                      loot={"credentials": 2, "files": 5},
                      notebook_entries=[1, 2, 3], now=2000.0)
    assert closed.status == "closed" and closed.closed_at == 2000.0
    assert closed.counts["actions"] == 2
    assert closed.counts["loot_credentials"] == 2 and closed.counts["loot_files"] == 5
    assert closed.counts["notebook"] == 3
    assert closed.report is not None and closed.report["integrity"] == "CLEAN"


def test_close_flags_unauthorized_action(tmp_path):
    st = _store(tmp_path)
    scope = _scope()                         # only aa:bb:... is armed
    eng = st.open("lab run", scope=scope, now=1000.0)
    entries = [_entry(1, "deauth", bssid="de:ad:be:ef:00:00")]   # NOT in scope
    closed = st.close(eng.id, scope=scope, authlog_entries=entries, now=2000.0)
    assert closed.report["integrity"] == "FLAGGED"
    assert closed.counts["unauthorized"] == 1


def test_closing_an_already_closed_is_idempotent(tmp_path):
    st = _store(tmp_path)
    eng = st.open("x", scope=_scope(), now=1.0)
    st.close(eng.id, scope=_scope(), now=2.0)
    again = st.close(eng.id, scope=_scope(), now=3.0)
    assert again.status == "closed" and again.closed_at == 2.0   # unchanged


def test_after_close_a_new_one_can_open(tmp_path):
    st = _store(tmp_path)
    e1 = st.open("one", scope=_scope(), now=1.0)
    st.close(e1.id, scope=_scope(), now=2.0)
    assert st.active() is None
    assert st.open("two", scope=_scope(), now=3.0).name == "two"


def test_summary_and_list(tmp_path):
    st = _store(tmp_path)
    eng = st.open("case", operator="pat", authorization="lab", scope=_scope(), now=1000.0)
    summ = st.summary(eng.id)
    assert summ["found"] and summ["status"] == "active" and summ["armed_at_open"] == 1
    assert [e.id for e in st.list()] == [eng.id]
    assert st.summary("nope")["found"] is False
