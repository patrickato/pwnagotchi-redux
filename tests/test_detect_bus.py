"""Hardware-free tests for AlertBus + detect config (Grok backlog #5)."""
from __future__ import annotations

from redux.detect import (
    DEFAULTS,
    Alert,
    AlertBus,
    AlertKind,
    _opt,
    merge_options,
)


def _alert(
    ts: float,
    *,
    kind: AlertKind = AlertKind.DEAUTH_FLOOD,
    bssid: str = "aa:bb:cc:dd:ee:01",
    ssid: str = "",
    severity: str = "warning",
    reason: str = "test flood",
) -> Alert:
    return Alert(
        kind=kind,
        reason=reason,
        ts=ts,
        severity=severity,
        bssid=bssid,
        ssid=ssid,
    )


def test_opt_falls_back_to_defaults():
    assert _opt(None, "dedup_window_s") == DEFAULTS["dedup_window_s"]
    assert _opt({}, "escalate_after") == DEFAULTS["escalate_after"]


def test_opt_reads_override():
    assert _opt({"dedup_window_s": 12.5}, "dedup_window_s") == 12.5


def test_merge_options_applies_overrides():
    m = merge_options({"escalate_after": 9})
    assert m["escalate_after"] == 9
    assert m["dedup_window_s"] == DEFAULTS["dedup_window_s"]


def test_bus_passes_first_alert():
    bus = AlertBus({"dedup_window_s": 60.0, "escalate_after": 3})
    a = _alert(1.0)
    out = bus.publish(a)
    assert out is not None
    assert out.severity == "warning"
    assert out.reason == "test flood"


def test_bus_dedupes_within_window():
    bus = AlertBus({"dedup_window_s": 60.0, "escalate_after": 5})
    assert bus.publish(_alert(1.0)) is not None
    assert bus.publish(_alert(2.0)) is None  # suppressed
    assert bus.publish(_alert(3.0)) is None


def test_bus_allows_after_window_expires():
    bus = AlertBus({"dedup_window_s": 10.0, "escalate_after": 5})
    assert bus.publish(_alert(1.0)) is not None
    assert bus.publish(_alert(5.0)) is None
    # Outside window
    again = bus.publish(_alert(12.0))
    assert again is not None
    assert "escalated" not in again.reason


def test_bus_escalates_on_repeat_threshold():
    bus = AlertBus(
        {"dedup_window_s": 60.0, "escalate_after": 3, "escalate_to": "critical"}
    )
    assert bus.publish(_alert(1.0, severity="warning")) is not None
    assert bus.publish(_alert(2.0, severity="warning")) is None
    esc = bus.publish(_alert(3.0, severity="warning"))
    assert esc is not None
    assert esc.severity == "critical"
    assert "escalated" in esc.reason
    assert "3" in esc.reason


def test_bus_different_signatures_not_deduped():
    bus = AlertBus({"dedup_window_s": 60.0, "escalate_after": 5})
    a1 = bus.publish(_alert(1.0, bssid="11:11:11:11:11:11"))
    a2 = bus.publish(_alert(1.1, bssid="22:22:22:22:22:22"))
    assert a1 is not None and a2 is not None


def test_bus_publish_many():
    bus = AlertBus({"dedup_window_s": 60.0, "escalate_after": 3})
    alerts = [_alert(float(i), bssid="aa:aa:aa:aa:aa:01") for i in range(1, 5)]
    out = bus.publish_many(alerts)
    # first + escalated at 3rd; 2nd and 4th suppressed (4th after escalate still same window)
    assert len(out) >= 2
    assert out[0].severity == "warning"
    assert any(a.severity == "critical" for a in out)
