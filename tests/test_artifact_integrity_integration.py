"""Offline integration of cached capture integrity evidence into live Doctor."""
import time

from redux.core import live_runtime as live
from redux.crack.integrity_receipt import publish_receipt


def build(tmp_path):
    config = live.LiveConfig(state_dir=tmp_path / "state",
                             capture_dir=tmp_path / "incoming",
                             active_dir=tmp_path / "active", enable_web=False)
    return live.LiveRuntime(config, radio_probe=lambda: [])


def finding(runtime):
    return next(f for f in runtime._snapshot["doctor"]["findings"]
                if f["area"] == "capture artifact integrity")


def report(*, status="pass"):
    if status == "pass":
        return {"result": "pass", "checked": 1, "ready_rows": 1, "issues": {}}
    if status == "attention":
        return {"result": "attention", "checked": 1, "ready_rows": 1,
                "issues": {"artifact_missing": 1}}
    return {"result": "unknown", "checked": 0, "ready_rows": 0, "issues": {}}


def test_missing_integrity_receipt_is_unknown(tmp_path):
    runtime = build(tmp_path)
    try:
        runtime._checkpoint()
        assert runtime._snapshot["artifact_integrity"]["status"] == "unknown"
        assert finding(runtime)["status"] == "unknown"
    finally:
        runtime.close()


def test_verification_and_discrepancy_update_doctor_without_radio(tmp_path):
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    path = diagnostics / "artifact-integrity.json"
    runtime = build(tmp_path)
    try:
        publish_receipt(path, report(), completed_utc=time.time())
        runtime._checkpoint()
        assert runtime._snapshot["artifact_integrity"]["status"] == "ok"
        assert finding(runtime)["status"] == "ok"
        publish_receipt(path, report(status="attention"), completed_utc=time.time())
        runtime._checkpoint()
        assert runtime._snapshot["artifact_integrity"]["status"] == "attention"
        assert finding(runtime)["status"] == "action"
    finally:
        runtime.close()


def test_old_and_empty_receipts_never_look_verified(tmp_path):
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    path = diagnostics / "artifact-integrity.json"
    runtime = build(tmp_path)
    try:
        publish_receipt(path, report(), completed_utc=time.time() - 4000)
        runtime._checkpoint()
        assert runtime._snapshot["artifact_integrity"]["status"] == "unknown"
        assert finding(runtime)["status"] == "unknown"
        publish_receipt(path, report(status="unknown"), completed_utc=time.time())
        runtime._checkpoint()
        assert runtime._snapshot["artifact_integrity"]["reason"] == "no_ready_artifacts"
        assert finding(runtime)["status"] == "unknown"
    finally:
        runtime.close()


def test_checkpoint_never_hashes_prepared_artifacts(tmp_path, monkeypatch):
    from redux.crack import ingest
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    publish_receipt(diagnostics / "artifact-integrity.json",
                    report(), completed_utc=time.time())

    def refuse_digest(*args, **kwargs):
        raise AssertionError("checkpoint must not hash capture artifacts")
    monkeypatch.setattr(ingest, "_digest_file", refuse_digest)
    runtime = build(tmp_path)
    try:
        runtime._checkpoint()
        assert finding(runtime)["status"] == "ok"
    finally:
        runtime.close()


def test_unavailable_mount_suppresses_old_cached_success(tmp_path):
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    publish_receipt(diagnostics / "artifact-integrity.json",
                    report(), completed_utc=time.time())
    runtime = build(tmp_path)
    try:
        runtime.capture_mount_lost = True
        runtime._checkpoint()
        assert runtime._snapshot["artifact_integrity"]["status"] == "unknown"
        assert finding(runtime)["status"] == "unknown"
    finally:
        runtime.close()
