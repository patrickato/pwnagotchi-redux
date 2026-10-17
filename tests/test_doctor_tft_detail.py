"""Dedicated Doctor TFT rendering tests; real PIL rendering, fake telemetry."""
from redux.display import fbui


def status(now=100):
    return {
        "runtime": {"state": "running", "updated_utc": now},
        "doctor": {"overall": "degraded", "findings": []},
        "capture_processing": {"available": False},
    }


def test_fresh_findings_are_bounded_and_prioritized():
    report = status()
    report["doctor"]["findings"] = [
        {"status": "attention", "summary": "Disk  "},
        {"status": "action", "summary": "Storage missing"},
        {"status": "degraded", "summary": "Bettercap unavailable"},
        {"status": "attention", "summary": "Second warning"},
        {"status": "ok", "summary": "looks good"},
        {"status": "action", "summary": {"not": "text"}},
    ]
    view = fbui.status_view(report, now=101)
    assert [r["status"] for r in view["doctor_rows"]] == [
        "action", "action", "degraded"]
    assert view["doctor_rows"][1]["summary"] == "UNKNOWN"
    assert view["doctor_more"] == 2
    assert fbui.render(report, page="doctor", now=101).size == (480, 320)


def test_stale_doctor_summary_cannot_be_promoted_to_current_findings():
    report = status()
    report["doctor"]["findings"] = [
        {"status": "action", "summary": "Historical high-risk failure"}]
    view = fbui.status_view(report, now=300)
    assert view["stale"] is True
    assert view["doctor_rows"] == [] and view["doctor_more"] == 0
    assert view["health"] == "unknown"
    assert fbui.render(report, page="doctor", now=300).size == (480, 320)


def test_malformed_input_never_crashes_doctor_details():
    report = status()
    report["doctor"]["findings"] = [None, "bad", {}, {"status": True},
                                     {"status": "attention", "summary": None}]
    rows = fbui.status_view(report, now=101)["doctor_rows"]
    assert rows == [{"status": "attention", "summary": "UNKNOWN"}]
    assert fbui.render(report, page="doctor", now=101).size == (480, 320)
    report["doctor"]["findings"] = "not a list"
    assert fbui.status_view(report, now=101)["doctor_rows"] == []


def test_three_distinct_native_pages_render_from_one_measured_status():
    report = status()
    report["doctor"]["findings"] = [
        {"status": "degraded", "summary": "One issue"}]
    frames = [fbui.render(report, page=p, now=101).tobytes()
              for p in ("overview", "captures", "doctor")]
    assert len(set(frames)) == 3
