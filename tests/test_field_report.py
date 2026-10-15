"""Tests for zero-typing field diagnostics and their private snapshots."""
import importlib.util
from pathlib import Path
import os
import stat
import json

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
        "dashboard_active": True, "engine_attempts": 4,
        "failure_streak": 2, "retry_delay_seconds": 6,
        "retry_in_seconds": 4.5, "events_seen": 0, "handed_off": 2})
    monkeypatch.setattr(report, "command", lambda args, **kw: ["active"])
    monkeypatch.setattr(report, "log_tail", lambda **kw: ["bettercap: synthetic error"])
    monkeypatch.setattr(report, "api_status", lambda: "HTTP 401 (authentication required; API responding)")
    monkeypatch.setattr(report, "capture_mount_snapshot", lambda: (1, 2))
    text = report.snapshot()
    assert "live_state: degraded" in text
    assert "last_error: Bettercap exited unexpectedly" in text
    assert "dashboard: True" in text
    assert "engine_attempts: 4" in text
    assert "failure_streak: 2" in text
    assert "retry_delay_seconds: 6" in text
    assert "retry_in_seconds: 4.5" in text
    assert "events_seen: 0" in text
    assert "handed_off: 2" in text
    assert "bettercap_api_probe: HTTP 401" in text
    assert "bettercap: synthetic error" in text
    assert "/captures/redux/bettercap-live.cap" not in text


@pytest.mark.parametrize("handshakes, expected", [(None, "unobserved"), (0, "0"), (2, "2")])
def test_event_activity_does_not_substitute_for_handshake_observation(
        monkeypatch, handshakes, expected):
    state = {"events_seen": 168, "handed_off": 0}
    if handshakes is not None:
        state["handshake_events"] = handshakes
    monkeypatch.setattr(report, "read_json", lambda: state)
    monkeypatch.setattr(report, "command", lambda *args, **kw: ["active"])
    monkeypatch.setattr(report, "log_tail", lambda **kw: [])
    monkeypatch.setattr(report, "api_status", lambda: "HTTP 401")
    monkeypatch.setattr(report, "capture_mount_snapshot", lambda: (1, 2))
    text = report.snapshot()
    assert "events_seen: 168\n" in text
    assert f"handshake_events: {expected}\n" in text
    assert "handed_off: 0\n" in text


@pytest.mark.parametrize("value,expected", [("803\n", 803), ("801\n", 801),
                                          ("1\n", 1), ("broken", "unobserved"),
                                          ("65536", "unobserved")])
def test_link_type_is_measured_from_selected_interface(tmp_path, value, expected):
    iface = tmp_path / "wlan0"
    iface.mkdir()
    (iface / "type").write_text(value)
    assert report.capture_link_type("wlan0", net_root=tmp_path) == expected
    assert report.capture_link_type("wlan1", net_root=tmp_path) == "unobserved"


@pytest.mark.parametrize("iface", [None, "", "../wlan0", ".", "..", "wlan0;id"])
def test_invalid_interface_cannot_select_another_link_type(tmp_path, iface):
    (tmp_path / "type").write_text("803")
    assert report.capture_link_type(iface, net_root=tmp_path) == "unobserved"


def test_snapshot_reports_actual_selected_interface_link_type(monkeypatch):
    monkeypatch.setattr(report, "read_json", lambda: {"iface": "wlan0"})
    monkeypatch.setattr(report, "command", lambda *args, **kw: ["active"])
    monkeypatch.setattr(report, "log_tail", lambda **kw: [])
    monkeypatch.setattr(report, "api_status", lambda: "HTTP 401")
    monkeypatch.setattr(report, "capture_mount_snapshot", lambda: (1, 2))
    def probe(iface):
        assert iface == "wlan0"
        return 1
    monkeypatch.setattr(report, "capture_link_type", probe)
    assert "capture_link_type: 1\n" in report.snapshot()


def summary_fixture(monkeypatch):
    private = "PRIVATE_SSID_TOKEN"
    monkeypatch.setattr(report, "read_json", lambda: {
        "state": "running", "iface": "wlan0", "events_seen": 17,
        "handshake_events": 0, "handed_off": 0, "failure_streak": 0,
        "retry_in_seconds": 0, "last_error": private, "ssid": private,
        "api_password": private})
    monkeypatch.setattr(report, "capture_link_type", lambda iface: 1)
    monkeypatch.setattr(report, "capture_mount_snapshot", lambda: (1, 2))
    def command(args, **kw):
        if args[0] == "systemctl": return ["active"]
        if args[:3] == ["iw", "reg", "get"]: return ["global", "country US: DFS-FCC"]
        if args[0] == "rfkill":
            return ["0: phy0: Wireless LAN", " Soft blocked: no", " Hard blocked: no"]
        return ["Interface wlan0", " type monitor", " channel 6 (2437 MHz)", private]
    monkeypatch.setattr(report, "command", command)
    monkeypatch.setattr(report, "bounded_bytes", lambda path, limit:
                        b"US\n" if path.name == "regulatory-country" else
                        b'[live]\nrotation_seconds=300\nsecret="PRIVATE_SSID_TOKEN"\n')
    monkeypatch.setattr(report, "capture_inventory", lambda path:
                        {"available": True, "files": [], "truncated": False})
    monkeypatch.setattr(report, "read_summary", lambda path: {
        "available": True, "artifacts": 1, "by_status": {"ready": 1, private: 2},
        "reason": private, "last_scan": {"completed_utc": 100, "scanned": 1,
                                        "outcomes": {"ready": 1, private: 2}}})
    monkeypatch.setattr(report, "api_status", lambda: "HTTP 401")
    monkeypatch.setattr(report, "log_tail", lambda **kw: pytest.fail("summary must not read engine logs"))
    return private


def test_summary_uses_allowlisted_observations_without_private_text(monkeypatch, capsys):
    private = summary_fixture(monkeypatch)
    assert report.main(["--summary"]) == 0
    text = capsys.readouterr().out
    assert private not in text
    data = json.loads(text)
    assert data["summary_only"] is True
    assert data["runtime"]["error"] == "unclassified"
    assert data["runtime"]["handshake_events"] == 0
    assert data["radio"] == {"iface": "wlan0", "link_type": 1, "mode": "monitor", "channel": 6}
    assert data["regulatory"]["global_country"] == "US"
    assert data["rotation_seconds"] == 300
    assert data["processing"]["outcomes"] == {"ready": 1}
    assert data["processing"]["last_scan_utc"] == 100
    assert data["bettercap_http"] == 401


def test_summary_missing_and_malformed_values_stay_unobserved(monkeypatch):
    summary_fixture(monkeypatch)
    monkeypatch.setattr(report, "read_json", lambda: {
        "state": [], "events_seen": True, "handshake_events": float("nan"),
        "handed_off": 10**1000, "last_error": "selected regulatory country is not active"})
    monkeypatch.setattr(report, "capture_link_type", lambda iface: "unobserved")
    monkeypatch.setattr(report, "bounded_bytes", lambda *args: None)
    monkeypatch.setattr(report, "read_summary", lambda path: {"available": False, "reason": "PRIVATE_REASON"})
    monkeypatch.setattr(report, "command", lambda *args, **kw: ["unavailable"])
    monkeypatch.setattr(report, "api_status", lambda: "unavailable (URLError)")
    data = report.snapshot_summary()
    assert data["runtime"]["state"] is None
    assert data["runtime"]["events_seen"] is None
    assert data["runtime"]["handshake_events"] is None
    assert data["runtime"]["handed_off"] is None
    assert data["runtime"]["error"] == "regulatory_country_inactive"
    assert data["radio"]["iface"] is None
    assert data["regulatory"]["wifi_unblocked"] is None
    assert data["processing"]["last_scan_utc"] is None
    assert data["bettercap_http"] is None
    assert "PRIVATE_REASON" not in json.dumps(data, allow_nan=False)


def test_summary_and_full_log_mode_are_mutually_exclusive():
    with pytest.raises(SystemExit) as error:
        report.main(["--summary", "--full"])
    assert error.value.code == 2


def test_capture_inventory_never_reads_capture_payload_or_leaks_arbitrary_names(tmp_path):
    safe = tmp_path / "bettercap-0123456789abcdef.pcap"
    safe.write_bytes(b"PRIVATE_CAPTURE_PAYLOAD")
    (tmp_path / "PRIVATE_SSID.pcap").write_bytes(b"private")
    (tmp_path / "linked.pcap").symlink_to(safe)
    value = report.capture_inventory(tmp_path)
    text = json.dumps(value)
    assert value["available"] is True
    assert len(value["files"]) == 2
    assert "PRIVATE_SSID" not in text and "PRIVATE_CAPTURE_PAYLOAD" not in text
    assert {f["name"] for f in value["files"]} == {safe.name, "[filename withheld]"}
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    assert report.capture_inventory(alias)["available"] is False


def test_capture_inventory_is_bounded_and_marks_truncation(tmp_path):
    for index in range(5):
        (tmp_path / f"bettercap-{index:016x}.pcap").write_bytes(b"x")
    value = report.capture_inventory(tmp_path, limit=2)
    assert value["truncated"] is True
    assert len(value["files"]) == 2


def test_checkpoint_reader_refuses_symlink_fifo_and_oversize_inputs(tmp_path):
    source = tmp_path / "state.json"
    source.write_text('{"state":"running"}')
    assert report.read_json(source) == {"state": "running"}
    alias = tmp_path / "alias.json"
    alias.symlink_to(source)
    assert report.read_json(alias) == {}
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert report.read_json(fifo) == {}
    source.write_bytes(b"x" * 131073)
    assert report.read_json(source) == {}


def test_actual_checkpoint_read_stays_bounded_if_file_grows_after_stat(tmp_path, monkeypatch):
    source = tmp_path / "state.json"
    source.write_bytes(b"")
    fstat = report.os.fstat
    def grow(fd):
        prior = fstat(fd)
        source.write_bytes(b"x" * 20)
        return prior
    monkeypatch.setattr(report.os, "fstat", grow)
    assert report.bounded_bytes(source, 8) is None


@pytest.mark.parametrize("updated,expected", [
    (None, "unobserved"), (100, "fresh"), (88, "fresh"), (87.9, "stale"),
    (101, "future"), (True, "invalid"), ("100", "invalid"),
    (float("nan"), "invalid"), (float("inf"), "invalid"), (-1, "invalid"),
    (10**1000, "invalid"),
])
def test_checkpoint_freshness_requires_recent_real_timestamp(updated, expected):
    value = report.checkpoint_health(updated, now=100)
    assert value["status"] == expected
    assert value["stale_seconds"] == 12
    json.dumps(value, allow_nan=False)


def test_stale_running_checkpoint_is_exposed_in_both_report_modes(monkeypatch):
    summary_fixture(monkeypatch)
    monkeypatch.setattr(report, "read_json", lambda: {
        "state": "running", "updated_utc": 1, "iface": "wlan0"})
    monkeypatch.setattr(report.time, "time", lambda: 100)
    monkeypatch.setattr(report, "log_tail", lambda **kw: [])
    text = report.snapshot()
    assert "live_state: running\n" in text
    assert "checkpoint_freshness: stale\n" in text
    assert report.snapshot_summary()["checkpoint"]["status"] == "stale"


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



def test_report_link_probe_uses_bounded_reader_and_rejects_redirected_type(tmp_path):
    iface = tmp_path / "wlan0"
    iface.mkdir()
    target = tmp_path / "other-type"
    target.write_text("803\n")
    entry = iface / "type"
    entry.symlink_to(target)
    assert report.capture_link_type("wlan0", net_root=tmp_path) == "unobserved"
    entry.unlink()
    entry.write_text("803" + " " * 100000)
    assert report.capture_link_type("wlan0", net_root=tmp_path) == "unobserved"
