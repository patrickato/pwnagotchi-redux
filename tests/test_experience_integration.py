"""The offense -> experience loop records ONE honest outcome per attempt, and the
Brain reads history. No hardware: a fake driver + injected clock."""
import io
import contextlib
import json

from redux.core.experience import ExperienceStore, CaptureRecorder, Experience
from redux.core.brain import Brain
from redux.radio import Intent


# ---- CaptureRecorder: attempt correlates to exactly one outcome ---------- #

class _Ev:
    def __init__(self, type, data): self.type = type; self.data = data


class _Em:
    def __init__(self, event): self.payload = {"event": event}


def test_attempt_resolves_to_success_on_handshake(tmp_path):
    st = ExperienceStore(tmp_path / "e.jsonl")
    rec = CaptureRecorder(st, max_age=100.0)
    rec.note_attempt("AA:BB:CC:00:00:01", {"channel": 6}, now=1000.0)
    rec.observe(_Em(_Ev("handshake", {"bssid": "aa:bb:cc:00:00:01"})))
    rows = st.load()
    assert len(rows) == 1 and rows[0].outcome == "handshake" and rows[0].success
    assert rows[0].context["channel"] == 6          # context carried from the attempt


def test_attempt_resolves_to_miss_when_stale(tmp_path):
    st = ExperienceStore(tmp_path / "e.jsonl")
    rec = CaptureRecorder(st, max_age=60.0)
    rec.note_attempt("aa:bb:cc:00:00:02", {"channel": 11}, now=1000.0)
    assert rec.flush_stale(now=1030.0) == 0          # not stale yet
    assert rec.flush_stale(now=1100.0) == 1          # now resolved as a miss
    rows = st.load()
    assert len(rows) == 1 and rows[0].outcome == "none" and not rows[0].success


def test_no_double_count_one_row_per_attempt(tmp_path):
    st = ExperienceStore(tmp_path / "e.jsonl")
    rec = CaptureRecorder(st, max_age=100.0)
    # three attempts on ch6, one converts
    rec.note_attempt("a", {"channel": 6}, now=1.0)
    rec.note_attempt("b", {"channel": 6}, now=2.0)
    rec.note_attempt("c", {"channel": 6}, now=3.0)
    rec.observe(_Em(_Ev("handshake", {"bssid": "a"})))
    rec.flush_stale(now=1000.0)                      # b, c -> miss
    from redux.core.experience import aggregate
    agg = aggregate(st.load(), "channel")
    assert agg[6] == (1, 3)                          # 1 of 3 — a real rate, not 100%


# ---- Augur wires the recorder in lab mode -------------------------------- #

class _FakeDriver:
    def __init__(self): self.deauthed = []
    def set_interface(self, iface): pass
    def recon(self, on=True): pass
    def set_channels(self, channels): pass
    def arm_from_scope(self, scope): pass
    def poll_events(self, clear=True): return []
    def deauth(self, bssid, ssid=None): self.deauthed.append(bssid); return {}


def _lab_scope():
    from redux.core.scope import Scope
    return Scope(lab_mode=True)


def test_augur_records_offense_attempts_in_lab_mode(tmp_path):
    from redux.core import Augur
    from redux.radio import Radio
    from redux.geo import SightingStore
    st = ExperienceStore(tmp_path / "e.jsonl")
    alfa = Radio("wlan1", bands=frozenset({"2.4"}), monitor=True, inject=True, driver="mt76x2u")
    clock = {"t": 1000.0}
    agent = Augur([alfa], intent=Intent.HUNT, driver=_FakeDriver(),
                  scope=_lab_scope(), store=SightingStore(":memory:"), experience=st)
    agent._offense_clock = lambda: clock["t"]
    # device seen on channel 6
    agent._offense_known["aa:bb:cc:00:00:01"] = "TestNet"
    agent._offense_channel["aa:bb:cc:00:00:01"] = 6
    agent._drive_offense()
    rows = st.load()
    # an attempt is pending (not yet written) until it resolves
    assert rows == []
    # time passes with no handshake -> flush resolves it to a miss
    clock["t"] = 2000.0
    agent._recorder.flush_stale(clock["t"])
    rows = st.load()
    assert len(rows) == 1 and rows[0].context.get("channel") == 6
    assert rows[0].context.get("band") == "2.4"


# ---- Brain reads history honestly ---------------------------------------- #

def test_brain_historical_note_is_honest(tmp_path):
    st = ExperienceStore(tmp_path / "e.jsonl")
    # two captures + one miss at hour 9
    st.record("handshake", {"hour": 9}, ts=1.0)
    st.record("handshake", {"hour": 9}, ts=2.0)
    st.record("none", {"hour": 9}, ts=3.0)
    b = Brain(experience=st)
    note = b.historical_note(hour=9)
    assert "2/3" in note                             # sample size shown, not a bare %


def test_brain_without_experience_is_unchanged():
    b = Brain()
    assert b.historical_note() == ""
    # and recommend still works with no experience store
    d = b.recommend(current=Intent.HUNT)
    assert d.reason


# ---- CLI --------------------------------------------------------------- #

def _run(argv):
    from redux.cli import main
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = main(argv)
    return rc, out.getvalue()


def test_cli_learn_summary_and_rank(tmp_path):
    p = tmp_path / "e.jsonl"
    st = ExperienceStore(p)
    for _ in range(3):
        st.record("handshake", {"channel": 6})
    st.record("none", {"channel": 6})
    rc, out = _run(["learn", "--path", str(p), "summary"])
    assert rc == 0 and '"total": 4' in out
    rc, out = _run(["learn", "--path", str(p), "rank", "--dim", "channel", "--candidates", "6,11"])
    assert rc == 0 and "channel=6" in out and "channel=11" in out
    assert "untried" in out                          # ch11 flagged for exploration
