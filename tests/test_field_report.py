"""Tests for zero-typing field diagnostics and their private snapshots."""
import importlib.util
from pathlib import Path
import os
import stat

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/redux-field-report.py"
SPEC = importlib.util.spec_from_file_location("redux_field_report", SOURCE)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


def test_report_redacts_passwords_and_radio_ids():
    raw = ("set api.rest.password superSecret authorization: Basic encodedToken "
           "password: anotherSecret AA:BB:CC:DD:EE:FF")
    safe = report.clean(raw)
    assert "superSecret" not in safe
    assert "encodedToken" not in safe
    assert "anotherSecret" not in safe
    assert "AA:BB:CC:DD:EE:FF" not in safe
    assert safe.count("[REDACTED]") == 3
    assert "[MAC]" in safe


def test_log_tail_never_includes_caplet_or_unbounded_binary(tmp_path):
    log = tmp_path / "bettercap.log"
    log.write_text("hello\nset api.rest.password hidden\nerror from bettercap\n")
    value = report.log_tail(log)
    assert any("error from bettercap" in x for x in value)
    assert not any("hidden" in x for x in value)
    assert all(len(x) <= 250 for x in value)
    log.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    assert report.log_tail(log) == ["(engine log unavailable or exceeds limit)"]


def test_report_summary_uses_only_synthetic_service_data(monkeypatch):
    monkeypatch.setattr(report, "read_json", lambda: {
        "state": "degraded", "iface": None,
        "last_error": "Bettercap exited unexpectedly",
        "dashboard_active": True})
    monkeypatch.setattr(report, "command", lambda args, **kw: ["active"])
    monkeypatch.setattr(report, "log_tail", lambda **kw: ["bettercap: synthetic error"])
    monkeypatch.setattr(report, "api_status", lambda: "HTTP 401 (authentication required; API responding)")
    monkeypatch.setattr(report, "capture_mount_snapshot", lambda: (1, 2))
    text = report.snapshot()
    assert "live_state: degraded" in text
    assert "last_error: Bettercap exited unexpectedly" in text
    assert "dashboard: True" in text
    assert "bettercap_api_probe: HTTP 401" in text
    assert "bettercap: synthetic error" in text
    assert "/captures/redux/bettercap-live.cap" not in text


def test_private_atomic_save_requires_verified_capture_mount(tmp_path):
    output = tmp_path / "diagnostics/latest.txt"
    with pytest.raises(RuntimeError, match="REDUXCAP"):
        report.save("test", destination=output, mount_check=lambda: None)
    assert not output.exists()
    report.save("test\n", destination=output, mount_check=lambda: (1, 2))
    assert output.read_text() == "test\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
    report.save("updated\n", destination=output, mount_check=lambda: (1, 2))
    assert output.read_text() == "updated\n"
    assert not list(output.parent.glob(".report-*"))


def test_private_save_refuses_symlink_destination(tmp_path):
    outside = tmp_path / "secret"
    outside.write_text("do not overwrite")
    parent = tmp_path / "diagnostics"
    parent.mkdir()
    (parent / "latest.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        report.save("bad", destination=parent / "latest.txt",
                    mount_check=lambda: (1, 2))
    assert outside.read_text() == "do not overwrite"


def test_single_short_command_launchers_are_installed_from_source():
    stage = (Path(__file__).resolve().parents[1] /
             "image/stage-redux/00-redux/00-run.sh").read_text()
    assert 'redux-report"' in stage
    assert '"$ROOTFS_DIR/usr/local/bin/r"' in stage
    assert "systemctl enable redux-field-report.timer" in stage
    assert 'files/redux-field-report.service' in stage
    timer = (Path(__file__).resolve().parents[1] /
             "image/stage-redux/00-redux/files/redux-field-report.timer").read_text()
    assert "OnBootSec=2min" in timer
    assert "OnUnitActiveSec=15min" in timer

def test_api_probe_reports_auth_and_never_reads_sensitive_session(monkeypatch):
    from urllib.error import HTTPError
    import io

    class FakeOpener:
        def __init__(self, code):
            self.code = code
        def open(self, req, timeout):
            assert req.full_url == "http://127.0.0.1:8081/api/session"
            assert req.get_method() == "GET"
            assert not req.has_header("Authorization")
            assert timeout == 1.0
            raise HTTPError(req.full_url, self.code, "mock",
                            {}, io.BytesIO(b"api.rest.password VERY_PRIVATE"))
    assert "authentication required" in report.api_status(opener=FakeOpener(401))
    assert "HTTP 400 (unexpected" in report.api_status(opener=FakeOpener(400))
    assert "VERY_PRIVATE" not in report.api_status(opener=FakeOpener(400))


def test_api_probe_200_warns_about_auth_configuration_without_reading_body():
    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self):
            raise AssertionError("must not read an unauthenticated session dump")
    class FakeOpener:
        def open(self, req, timeout): return FakeResponse()
    assert "unauthenticated" in report.api_status(opener=FakeOpener())


def test_api_probe_ignores_environment_proxies(monkeypatch):
    class FakeOpener:
        def open(self, req, timeout):
            raise ConnectionRefusedError("synthetic unbound test port")
    def fake_build(*handlers):
        import urllib.request
        assert len(handlers) == 1
        assert isinstance(handlers[0], urllib.request.ProxyHandler)
        assert handlers[0].proxies == {}
        return FakeOpener()
    monkeypatch.setattr(report.urllib.request, "build_opener", fake_build)
    assert "unavailable" in report.api_status()

