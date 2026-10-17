"""Volatile real-status trend regressions; synthetic times, no Pi or RF access."""
import math

import pytest

from redux.display import fbui, telemetry_history as history
from redux.display import trend_chart, touch_nav, preview


def report(at, events=0, hs=0, handoffs=0, *, pid=47, iface="wlan0",
           state="running"):
    return {"runtime": {"updated_utc": at, "state": state, "iface": iface,
                        "pid": pid, "events_seen": events,
                        "handshake_events": hs, "handed_off": handoffs},
            "doctor": {"overall": "unknown", "findings": []}}


def test_two_fresh_status_samples_produce_real_per_minute_derivatives():
    h = history.TelemetryWindow()
    assert h.ingest(report(1000, 10, 1, 2), now=1000)
    assert h.snapshot(now=1000)["state"] == "ONE SAMPLE"
    assert h.ingest(report(1002, 20, 3, 3), now=1002)
    snapshot = h.snapshot(now=1002)
    assert snapshot["state"] == "TRACKING"
    assert snapshot["seconds"] == 2
    assert snapshot["last"] == {
        "age_seconds": 0, "events_per_min": 300,
        "hs_events_per_min": 60, "handoffs_per_min": 30,
    }


def test_identical_server_timestamp_does_not_invent_an_interval():
    h = history.TelemetryWindow()
    assert h.ingest(report(1000, 10), now=1000)
    assert not h.ingest(report(1000, 10), now=1001)
    assert len(h.samples) == 1 and not h.rates
    assert not h.ingest(report(1000, 99), now=1001)
    assert not h.samples and h.state == "CLOCK CONFLICT"


def test_counter_reset_and_engine_change_break_series():
    h = history.TelemetryWindow()
    h.ingest(report(1000, 100), now=1000)
    h.ingest(report(1002, 130), now=1002)
    assert h.snapshot(now=1002)["state"] == "TRACKING"
    h.ingest(report(1004, 1), now=1004)
    assert h.state == "COUNTER RESET" and not h.rates
    h.ingest(report(1006, 2), now=1006)
    assert h.snapshot(now=1006)["state"] == "TRACKING"
    h.ingest(report(1008, 5, pid=99), now=1008)
    assert h.state == "ENGINE CHANGED"
    assert len(h.samples) == 1 and not h.rates
    h.ingest(report(1010, 9, pid=99, iface="wlan1"), now=1010)
    assert h.state == "ENGINE CHANGED"


def test_stale_runtime_unqualified_interface_and_gap_never_bridge_old_data():
    h = history.TelemetryWindow()
    h.ingest(report(1000, 100), now=1000)
    h.ingest(report(1002, 105), now=1002)
    assert h.snapshot(now=1050)["points"] == []
    assert h.snapshot(now=1050)["state"] == "STALE"
    h.ingest(report(1060, 110), now=1060)
    assert len(h.samples) == 1 and h.state == "SAMPLE GAP"
    assert not h.ingest(report(1062, 113, iface="../../oops"), now=1062)
    assert h.state == "UNQUALIFIED" and not h.samples
    assert not h.ingest(report(1060, 110), now=1100)
    assert h.state == "NOT CURRENT"


@pytest.mark.parametrize("key,bad", [
    ("events_seen", True), ("handed_off", -1),
    ("handshake_events", "3"), ("events_seen", float("nan")),
    ("events_seen", 10**1000), ("handed_off", None),
])
def test_malformed_or_unknown_counts_do_not_draw_false_activity(key, bad):
    h = history.TelemetryWindow()
    good = report(1000, 10, 1, 0)
    h.ingest(good, now=1000)
    x = report(1002, 15, 2, 1)
    x["runtime"][key] = bad
    assert not h.ingest(x, now=1002)
    assert h.state == "COUNTERS UNKNOWN"
    assert h.snapshot(now=1002)["points"] == []


@pytest.mark.parametrize("time_value", [True, -1, float("nan"), 10**1000])
def test_broken_sample_clocks_fail_closed(time_value):
    h = history.TelemetryWindow()
    assert not h.ingest(report(time_value, 1), now=1001)
    assert h.snapshot(now=1001)["points"] == []
    assert not h.ingest(report(1000, 1), now=time_value)


def test_bounded_memory_and_five_minute_immutable_rate_history():
    h = history.TelemetryWindow()
    for i in range(200):
        at = 1000 + 2 * i
        assert h.ingest(report(at, events=i * 2), now=at)
    assert len(h.samples) == 151
    assert len(h.rates) == 150
    snapshot = h.snapshot(now=1398)
    assert len(snapshot["points"]) == 150
    assert all(0 <= row["age_seconds"] <= 300 for row in snapshot["points"])
    assert snapshot["points"][0]["age_seconds"] >= 295
    assert snapshot["last"]["events_per_min"] == 60
    snapshot["points"].clear()
    assert len(h.rates) == 150


def test_discontinuous_time_or_broken_engine_never_carries_old_rates():
    h = history.TelemetryWindow()
    h.ingest(report(1000, 1), now=1000)
    h.ingest(report(1002, 3), now=1002)
    assert not h.ingest(report(1004, 3, state="degraded"), now=1004)
    assert h.state == "NOT CURRENT" and not h.rates
    h.ingest(report(1006, 7), now=1006)
    h.ingest(report(1008, 11), now=1008)
    assert h.snapshot(now=1008)["state"] == "TRACKING"
    h.ingest(report(1007, 12), now=1008)
    assert h.state == "SAMPLE GAP"
    assert not h.rates


def test_native_trends_render_uses_real_plot_only_when_history_supplied():
    h = history.TelemetryWindow()
    h.ingest(report(1000, 5), now=1000)
    h.ingest(report(1002, 10), now=1002)
    h.ingest(report(1004, 20), now=1004)
    data = h.snapshot(now=1004)
    assert len(trend_chart.points(data)) == 2
    shown = fbui.render(report(1004, 20), page="trends", now=1004,
                        trends=data)
    unknown = fbui.render(report(1004, 20), page="trends", now=1004)
    assert shown.size == unknown.size == (480, 320)
    assert shown.tobytes() != unknown.tobytes()
    assert preview.build(report=report(1004, 20), pages=("trends",))["trends"].startswith(
        bytes.fromhex("89504e470d0a1a0a"))


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(state="STALE"),
    lambda d: d["points"][0].update(events_per_min=-1),
    lambda d: d["points"][0].update(events_per_min=10**1000),
    lambda d: d["points"][0].update(age_seconds=400),
    lambda d: d["points"][1].update(age_seconds=999),
    lambda d: d["points"][0].update(handoffs_per_min=float("nan")),
    lambda d: d.update(points="not a list"),
])
def test_chart_rejects_corrupted_input_without_partial_curve(mutation):
    h = history.TelemetryWindow()
    for i in range(3):
        h.ingest(report(1000 + i * 2, 10 + i), now=1000 + i * 2)
    view = h.snapshot(now=1004)
    mutation(view)
    assert trend_chart.points(view) == []
    assert fbui.render(None, page="trends", trends=view).size == (480, 320)


def test_five_page_swipe_wraps_correctly():
    assert touch_nav.navigate("radios", 1) == "trends"
    assert touch_nav.navigate("trends", 1) == "overview"
    assert touch_nav.navigate("overview", -1) == "trends"


def test_native_refresh_loop_accumulates_real_status_samples(monkeypatch):
    from time import time
    current = time()
    responses = [report(current - 2, events=2),
                 report(current, events=6)]
    shown = []
    monkeypatch.setattr(fbui, "framebuffer_info",
                        lambda *_a: {"ok": True, "stride": 960})
    monkeypatch.setattr(fbui, "read_status", lambda *_a: responses.pop(0))
    monkeypatch.setattr(fbui.time, "monotonic", iter((0, 3)).__next__)
    renderer = fbui.render
    def draw(record, *, page, **kw):
        assert page == "trends"
        shown.append(kw.get("trends"))
        return renderer(record, page=page, **kw)
    monkeypatch.setattr(fbui, "render", draw)
    monkeypatch.setattr(fbui, "rgb565_bytes", lambda *_a, **_k: b"frame")
    writes = []
    def writer(*_a):
        writes.append(1)
        if len(writes) == 2:
            raise RuntimeError("finished synthetic loop")
    monkeypatch.setattr(fbui, "write_frame", writer)
    with pytest.raises(RuntimeError, match="finished synthetic loop"):
        fbui.main(["--framebuffer", "/dev/fb1", "--run", "--page", "trends"])
    assert shown[0]["points"] == []
    assert shown[0]["state"] == "ONE SAMPLE"
    assert shown[1]["state"] == "TRACKING"
    assert shown[1]["last"]["events_per_min"] == 120
    assert len(writes) == 2


def test_trend_legend_shows_latest_observed_rates():
    class DrawRecorder:
        def __init__(self):
            self.labels = []
        def text(self, xy, label, **kwargs):
            self.labels.append(label)
        def line(self, *args, **kwargs):
            pass
        def ellipse(self, *args, **kwargs):
            pass
        def rectangle(self, *args, **kwargs):
            pass
    h = history.TelemetryWindow()
    h.ingest(report(1000, events=5), now=1000)
    h.ingest(report(1002, events=10), now=1002)
    h.ingest(report(1004, events=20), now=1004)
    draw = DrawRecorder()
    trend_chart.draw_trends(
        draw, h.snapshot(now=1004), font=lambda *_a, **_k: None,
        ink=None, subtle=None, teal=None, amber=None, blue=None)
    assert "EVENTS 300" in draw.labels
    assert "HS EVENTS 0.0" in draw.labels
    assert "HANDOFFS 0.0" in draw.labels
