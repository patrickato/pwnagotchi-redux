"""Hardware-free tests for threat-report aggregator (Grok DD5)."""
from redux.detect.alerts import Alert, AlertKind
from redux.detect.report import ThreatReport, build_threat_report


def _a(kind: AlertKind, sev: str, ts: float, reason: str = "x") -> Alert:
    return Alert(kind=kind, reason=reason, ts=ts, severity=sev, bssid="aa:aa:aa:aa:aa:01")


def test_severity_sort_critical_first():
    alerts = [
        _a(AlertKind.PMF_MISSING, "info", 1.0, "pmf"),
        _a(AlertKind.DEAUTH_FLOOD, "critical", 2.0, "flood"),
        _a(AlertKind.KARMA, "warning", 3.0, "karma"),
    ]
    r = build_threat_report(alerts)
    assert r.total == 3
    assert r.alerts[0].severity == "critical"
    assert "flood" in r.summary


def test_time_window_filter():
    alerts = [
        _a(AlertKind.DEAUTH_FLOOD, "warning", 10.0),
        _a(AlertKind.DEAUTH_FLOOD, "warning", 50.0),
    ]
    r = build_threat_report(alerts, since_ts=20.0, until_ts=60.0)
    assert r.total == 1
    assert r.alerts[0].ts == 50.0


def test_empty_report():
    r = build_threat_report([])
    assert r.total == 0
    assert "no alerts" in r.summary.lower()
    assert isinstance(r, ThreatReport)


def test_to_text_readable():
    alerts = [_a(AlertKind.ROGUE_AP, "critical", 1.0, "evil twin on Home")]
    text = build_threat_report(alerts).to_text()
    assert "critical" in text
    assert "evil twin" in text
    assert "rogue_ap" in text


def test_counts():
    alerts = [
        _a(AlertKind.DEAUTH_FLOOD, "critical", 1.0),
        _a(AlertKind.DEAUTH_FLOOD, "warning", 2.0),
        _a(AlertKind.PMF_MISSING, "info", 3.0),
    ]
    r = build_threat_report(alerts)
    assert r.counts_by_severity["critical"] == 1
    assert r.counts_by_kind["deauth_flood"] == 2
