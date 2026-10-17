"""Measured capture ledger / worker heartbeat UI regressions (no Pi hardware)."""
import copy

import pytest

from redux.display import capture_panel, fbui, preview


def report():
    return {
        "runtime": {"state": "running", "updated_utc": 1000,
                    "handed_off": 17, "capture_link_type": 803},
        "doctor": {"overall": "attention", "findings": []},
        "capture_processing": {
            "available": True,
            "artifacts": 18, "by_status": {"ready": 8, "error": 2,
                                            "invalid": 5, "duplicate": 3},
            "hash_records": 12, "last_scan": {
                "completed_utc": 960, "scanned": 7,
                "outcomes": {"ready": 2, "error": 1,
                             "deferred": 1, "deferred_low_storage": 2,
                             "duplicate": 1}},
        },
    }


def test_recent_scan_distinct_from_cumulative_ledger():
    panel = capture_panel.model(report(), now=1001)
    assert panel["ledger"] == "AVAILABLE"
    assert (panel["ready"], panel["errors"], panel["hash_records"]) == (8, 2, 12)
    assert panel["artifacts"] == 18
    assert panel["scan_state"] == "RECENT"
    assert panel["scan_age_seconds"] == 41
    assert panel["scan_files"] == 7
    assert (panel["last_ready"], panel["last_errors"], panel["last_deferred"]) == (2, 1, 3)


def test_stale_runtime_cannot_expose_old_capture_totals_as_current():
    panel = capture_panel.model(report(), now=1100)
    assert panel["ledger"] == "STALE"
    assert panel["ready"] is None and panel["hash_records"] is None
    assert panel["scan_state"] == "UNVERIFIED"
    assert panel["last_ready"] is None


def test_recent_runtime_does_not_freshen_old_worker_scan():
    r = report()
    r["runtime"]["updated_utc"] = 2000
    panel = capture_panel.model(r, now=2000)
    assert panel["ledger"] == "AVAILABLE"
    assert panel["ready"] == 8
    assert panel["scan_state"] == "STALE"
    assert panel["scan_files"] is None


def test_worker_not_present_is_unknown_not_false_success():
    r = report()
    r["capture_processing"]["last_scan"] = None
    panel = capture_panel.model(r, now=1001)
    assert panel["scan_state"] == "NO COMPLETED SCAN"
    assert panel["last_ready"] is None


@pytest.mark.parametrize("bad", [False, -1, 1.5, 10**1000, "7"])
def test_invalid_ledger_count_never_becomes_tft_metric(bad):
    r = report()
    r["capture_processing"]["by_status"]["ready"] = bad
    assert capture_panel.model(r, now=1001)["ready"] is None
    assert capture_panel.compact_count(bad) == "--"


@pytest.mark.parametrize("bad", [False, -1, "3", float("nan"), 10**1000])
def test_invalid_worker_counts_reject_heartbeat(bad):
    r = report()
    r["capture_processing"]["last_scan"]["outcomes"]["ready"] = bad
    assert capture_panel.model(r, now=1001)["scan_state"] == "INVALID"
    assert capture_panel.model(r, now=1001)["last_ready"] is None


@pytest.mark.parametrize("bad", [None, "1000", float("nan"), 10**1000])
def test_invalid_runtime_clock_never_qualifies_capture_ledger(bad):
    r = report()
    r["runtime"]["updated_utc"] = bad
    assert capture_panel.model(r, now=1001)["ledger"] == "STALE"


def test_unavailable_ledger_exposes_no_cumulative_metrics():
    r = report()
    r["capture_processing"]["available"] = False
    panel = capture_panel.model(r, now=1001)
    assert panel["ledger"] == "UNAVAILABLE"
    assert panel["ready"] is None and panel["scan_state"] == "UNVERIFIED"


def test_compact_counts_are_bounded_and_have_no_synthetic_zeros():
    assert capture_panel.compact_count(None) == "--"
    assert capture_panel.compact_count(0) == "0"
    assert capture_panel.compact_count(10001) == "10K"
    assert len(capture_panel.compact_count(2**63 - 1)) <= 6


def test_actual_captures_page_renders_without_exposing_private_payloads():
    r = report()
    r["capture_processing"]["secret"] = "PRIVATE-DO-NOT-DISPLAY"
    page = fbui.render(r, page="captures", now=1001)
    assert page.size == (480, 320)
    assert page.mode == "RGB"
    assert fbui.render(r, page="overview", now=1001).tobytes() != page.tobytes()
    assert preview.build(report=r, pages=("captures",))["captures"].startswith(
        bytes.fromhex("89504e470d0a1a0a"))


def test_missing_report_and_malformed_report_render_without_exceptions():
    for report_value in (None, {}, {"capture_processing": "bad", "runtime": []},
                         {"runtime": {"updated_utc": 1000}, "capture_processing":
                          {"available": True, "by_status": []}}):
        assert fbui.render(report_value, page="captures", now=1001).size == (480, 320)
