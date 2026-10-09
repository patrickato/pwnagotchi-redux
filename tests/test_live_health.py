"""Doctor/live bridge: only actual observations can turn a field green."""
from redux.core.live_health import describe_live_health


def finding(report, area):
    return next(f for f in report["findings"] if f["area"] == area)


def test_running_health_uses_actual_interface_and_storage():
    r = describe_live_health("running", iface="wlan1mon",
                             free_bytes=1024 * 1024 * 1024,
                             reserve_bytes=64 * 1024 * 1024,
                             handoffs=2)
    assert finding(r, "live engine")["status"] == "ok"
    assert finding(r, "capture storage")["status"] == "ok"
    assert finding(r, "capture handoff")["status"] == "ok"
    assert "thermal/power" in r["coverage"]["not_assessed"]
    assert r["overall"] == "ok"  # explicitly checked areas passed; gaps exposed


def test_running_before_first_capture_does_not_claim_verified_handoff():
    r = describe_live_health("running", iface="wlan1mon",
                             free_bytes=128_000_000, reserve_bytes=10_000_000)
    assert finding(r, "capture handoff")["status"] == "unknown"
    assert "capture handoff" in r["coverage"]["not_assessed"]


def test_capture_failure_and_missing_space_are_not_green():
    r = describe_live_health("degraded", free_bytes=None,
                             last_error="No monitor-capable radio")
    assert r["overall"] == "degraded"
    assert "No monitor-capable radio" in finding(r, "live engine")["reason"]
    assert finding(r, "capture storage")["status"] == "unknown"
    assert "capture storage" in r["coverage"]["not_assessed"]


def test_storage_exhaustion_and_handoff_error_have_different_remedies():
    r = describe_live_health("storage_paused", free_bytes=1000, reserve_bytes=64_000,
                             handoff_error="synthetic I/O failure")
    assert r["overall"] == "action"
    assert finding(r, "capture storage")["status"] == "action"
    assert finding(r, "capture handoff")["status"] == "degraded"
    assert "incoming" in finding(r, "capture handoff")["remediation"]


def test_stopped_engine_is_never_labeled_healthy():
    r = describe_live_health("stopped", free_bytes=128_000, reserve_bytes=1000)
    assert r["overall"] == "attention"
    assert finding(r, "live engine")["status"] == "attention"


def test_augur_findings_preserved_and_worst_severity_wins():
    report = {
        "overall": "action", "label": "ACTION REQUIRED",
        "findings": [{
            "area": "capture radio", "status": "action",
            "summary": "Missing radio", "reason": "real probe",
            "remediation": "Connect supported radio", "detail": {},
        }],
        "coverage": {"assessed": ["capture radio"], "not_assessed": []},
    }
    result = describe_live_health("running", iface="wlan1mon",
                                  free_bytes=100000, reserve_bytes=1000,
                                  doctor_report=report)
    assert result["overall"] == "action"
    assert finding(result, "capture radio")["summary"] == "Missing radio"
    assert finding(result, "live engine")["status"] == "ok"


def test_live_runtime_checkpoint_exposes_doctor_to_api_and_file(tmp_path):
    import json
    from redux.core.live_runtime import LiveConfig, LiveRuntime
    conf = LiveConfig(state_dir=tmp_path / "state",
                      active_dir=tmp_path / "active",
                      capture_dir=tmp_path / "incoming", enable_web=False)
    runtime = LiveRuntime(conf, radio_probe=lambda: [])
    try:
        runtime.tick()
        assert runtime._snapshot["doctor"]["overall"] == "degraded"
        assert finding(runtime._snapshot["doctor"], "capture storage")["status"] == "ok"
        assert "thermal/power" in runtime._snapshot["doctor"]["coverage"]["not_assessed"]
        saved = json.loads((tmp_path / "state" / "live.json").read_text())
        assert saved["health"] == "degraded"
        assert saved["health_unknown_areas"] > 0
    finally:
        runtime.close()
