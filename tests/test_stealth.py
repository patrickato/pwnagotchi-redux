"""Quiet/Ghost mode — the stealth posture: passive-first, frame budget, footprint."""
from redux.core.stealth import (
    QuietConfig, quiet_method_order, footprint_score, transmit_allowed, status,
)


def test_config_roundtrip_and_fail_closed(tmp_path):
    p = tmp_path / "stealth.json"
    QuietConfig(enabled=True, frame_budget=5).save(p)
    q = QuietConfig.load(p)
    assert q.enabled is True and q.frame_budget == 5
    # garbage / wrong types never silently enable or set a budget
    p.write_text('{"enabled": "true", "frame_budget": "lots"}')
    q2 = QuietConfig.load(p)
    assert q2.enabled is False and q2.frame_budget == 0
    # a non-dict top-level is not a config
    p.write_text("[]")
    assert QuietConfig.load(p).enabled is False
    # missing file → defaults (quiet off)
    assert QuietConfig.load(tmp_path / "nope.json").enabled is False


def test_quiet_prefers_zero_transmit_methods():
    order = quiet_method_order(["deauth", "angryoxide", "pmkid", "tcpdump-lock"])
    # the two silent methods come first, the loudest (deauth) last
    assert order[0] in ("pmkid", "tcpdump-lock") and order[1] in ("pmkid", "tcpdump-lock")
    assert order[-1] == "deauth"


def test_footprint_score_buckets():
    assert footprint_score([])["label"] == "silent"                      # pure listen
    assert footprint_score([{"kind": "probe", "frames": 2}])["label"] == "quiet"
    assert footprint_score([{"kind": "deauth", "frames": 20}])["label"] == "loud"
    assert footprint_score([])["score"] == 0


def test_transmit_gate_enforces_budget_and_confirm():
    off = QuietConfig(enabled=False)
    assert transmit_allowed(off)[0] is True                              # off → existing gates only

    q = QuietConfig(enabled=True, frame_budget=3, require_confirm=True)
    assert transmit_allowed(q, frames_so_far=1)[0] is True               # within budget
    ok, why = transmit_allowed(q, frames_so_far=3, confirmed=False)
    assert ok is False and "confirm" in why                             # budget hit, no confirm → refused
    assert transmit_allowed(q, frames_so_far=3, confirmed=True)[0] is True   # confirmed → allowed


def test_status_block_for_the_ui():
    s = status(QuietConfig(enabled=True, frame_budget=0),
               recent_events=[{"kind": "deauth", "frames": 1}])
    assert s["quiet"] is True and s["frame_budget"] == 0
    assert "footprint" in s and s["footprint"]["label"] in ("silent", "quiet", "moderate", "loud")
