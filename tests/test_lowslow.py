"""Low-and-slow scheduler — pace emissions under a footprint target, jittered."""
from redux.core.lowslow import LowAndSlowScheduler, LowSlowConfig, Decision
from redux.core.stealth import QuietConfig


def _sched(**cfg):
    return LowAndSlowScheduler(config=LowSlowConfig(**cfg), seed=1)


def test_first_emission_is_permitted():
    s = _sched()
    d = s.permit(100.0, kind="deauth", frames=1)
    assert d.allowed and d.wait_seconds == 0.0


def test_min_gap_blocks_back_to_back():
    s = _sched(min_gap_seconds=8.0, jitter_seconds=0.0)
    s.record(100.0, kind="deauth")
    d = s.permit(101.0, kind="deauth")        # 1s later — inside the 8s gap
    assert not d.allowed and 6.9 < d.wait_seconds <= 7.0
    # after the gap, it's clear again
    assert s.permit(108.5, kind="deauth").allowed


def test_window_cap_throttles_sustained_activity():
    # cap of 10 weighted; deauth=5 each → only 2 fit per window
    s = _sched(window_seconds=300.0, max_weighted_per_window=10,
               min_gap_seconds=0.0, jitter_seconds=0.0)
    assert s.permit(0.0, kind="deauth").allowed
    s.record(0.0, kind="deauth")              # weight 5
    assert s.permit(1.0, kind="deauth").allowed
    s.record(1.0, kind="deauth")              # weight 10 — window full
    d = s.permit(2.0, kind="deauth")
    assert not d.allowed and "window cap" in d.reason
    # the first event ages out at t=300 → room frees then
    assert round(d.wait_seconds) == 298


def test_silent_methods_are_not_throttled_much():
    # pmkid/eapol are weight 1; many fit before the cap
    s = _sched(max_weighted_per_window=14, min_gap_seconds=0.0, jitter_seconds=0.0)
    for t in range(10):
        d = s.permit(float(t), kind="eapol", frames=1)
        assert d.allowed
        s.record(float(t), kind="eapol", frames=1)
    assert s.window_weight(10.0) == 10        # 10 weighted, still under 14


def test_quiet_mode_gate_is_honoured_not_loosened():
    s = _sched(max_weighted_per_window=100)   # scheduler itself would allow
    quiet = QuietConfig(enabled=True, frame_budget=0, require_confirm=True)
    d = s.permit(0.0, kind="deauth", quiet=quiet, confirmed=False)
    assert not d.allowed and "quiet" in d.reason.lower()
    # an explicit confirm satisfies Quiet's over-budget rule
    assert s.permit(0.0, kind="deauth", quiet=quiet, confirmed=True).allowed


def test_jitter_varies_the_cadence():
    s = _sched(min_gap_seconds=8.0, jitter_seconds=6.0)
    gaps = []
    t = 0.0
    for _ in range(5):
        s.record(t, kind="deauth")
        gap = s._next_allowed - t
        gaps.append(gap)
        t = s._next_allowed
    assert all(8.0 <= g <= 14.0 for g in gaps)
    assert len(set(round(g, 3) for g in gaps)) > 1     # not a constant interval


def test_next_slot_predicts_earliest_time():
    s = _sched(min_gap_seconds=10.0, jitter_seconds=0.0)
    s.record(100.0, kind="deauth")
    assert s.next_slot(101.0, kind="deauth") == 110.0  # 100 + 10s gap


def test_status_reports_headroom_and_footprint():
    s = _sched(max_weighted_per_window=14)
    s.record(0.0, kind="deauth")              # weight 5
    st = s.status(1.0)
    assert st["window_weight"] == 5 and st["headroom"] == 9
    assert st["footprint"]["label"] in ("quiet", "moderate")
    assert "pacing to stay" in st["note"]
