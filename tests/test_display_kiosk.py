"""Display kiosk preflight: fail closed for uncertain TFT and X11 state."""
import json
from pathlib import Path
import stat
from types import SimpleNamespace
import urllib.error

import pytest

from redux.display import kiosk


class FakeResponse:
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def read(self, count):
        return self.data[:count]


def screen(tmp_path, name="fb_ili9486", size="480,320"):
    folder = tmp_path / "fb1"
    folder.mkdir()
    (folder / "name").write_text(name)
    (folder / "virtual_size").write_text(size)
    return tmp_path


def device(_):
    return SimpleNamespace(st_mode=stat.S_IFCHR | 0o600)


def status(url, timeout):
    assert url == "http://127.0.0.1:8080/api/status"
    assert timeout == 2
    return FakeResponse(b'{"runtime":{"state":"running"},"doctor":{"overall":"unknown"}}')


def preflight(tmp_path, **changes):
    opts = {
        "environ": {"DISPLAY": ":0"},
        "uid": 1000,
        "which": lambda x: "/usr/bin/chromium" if x == "chromium" else None,
        "sys_graphics": screen(tmp_path),
        "statter": device,
        "opener": status,
    }
    opts.update(changes)
    return kiosk.preflight("/dev/fb1", **opts)


def test_real_preflight_has_explicit_honest_boundaries(tmp_path):
    result = preflight(tmp_path)
    assert result["ok"] is True
    assert result["checks"]["framebuffer"]["resolution"] == [480, 320]
    assert result["checks"]["framebuffer"]["name"] == "fb_ili9486"
    assert result["checks"]["browser"]["executable"] == "/usr/bin/chromium"
    assert result["touch_verified"] is False
    assert result["physical_display_verified"] is False
    assert "does not prove" in result["reason"]


@pytest.mark.parametrize("size", ["1920,1080", "800,480", "invalid", "0,0"])
def test_unexpected_or_invalid_framebuffer_resolution_refused(tmp_path, size):
    root = screen(tmp_path, size=size)
    result = kiosk.framebuffer_probe("/dev/fb1", sys_graphics=root,
                                     statter=device)
    assert result["ok"] is False
    assert result["reason"]


def test_portrait_orientation_accepted_without_claiming_rotation(tmp_path):
    root = screen(tmp_path, size="320,480")
    result = kiosk.framebuffer_probe("/dev/fb1", sys_graphics=root,
                                     statter=device)
    assert result["ok"]
    assert result["resolution"] == [320, 480]
    assert kiosk._ALLOWED == {(320, 480), (480, 320)}


def test_missing_or_noncharacter_framebuffer_fails_closed(tmp_path):
    screen(tmp_path)
    assert not kiosk.framebuffer_probe("/dev/fb0", sys_graphics=tmp_path,
                                       statter=device)["ok"]
    for invalid in ("/dev/null", "../dev/fb1", "/tmp/fb1", "/dev/fb1;rm -rf /"):
        assert not kiosk.framebuffer_probe(invalid, sys_graphics=tmp_path,
                                           statter=device)["ok"]
    regular = lambda _: SimpleNamespace(st_mode=stat.S_IFREG | 0o644)
    error = kiosk.framebuffer_probe("/dev/fb1", sys_graphics=tmp_path,
                                    statter=regular)
    assert not error["ok"] and "character" in error["reason"]
    missing = kiosk.framebuffer_probe("/dev/fb1", sys_graphics=tmp_path,
                                      statter=lambda _: (_ for _ in ()).throw(
                                          FileNotFoundError()))
    assert not missing["ok"]


def test_runtime_sessions_and_browser_are_required(tmp_path):
    assert not preflight(tmp_path, uid=0)["ok"]
    assert not preflight(tmp_path, environ={})["ok"]
    assert not preflight(tmp_path, which=lambda _: None)["ok"]
    for field in ("browser", "session"):
        assert not preflight(tmp_path, **{
            "which": lambda _: None
        } if field == "browser" else {"environ": {}})["checks"][field]["ok"]


@pytest.mark.parametrize("data", [
    b"{}", b"[]", b"not json",
    b'{"state":"running"}', b'x' * (65536 + 1),
])
def test_kiosk_rejects_nonredux_or_oversized_local_dashboard(tmp_path, data):
    result = preflight(tmp_path, opener=lambda *_a, **_k: FakeResponse(data))
    assert not result["ok"]
    assert not result["checks"]["dashboard"]["ok"]


def test_disconnected_dashboard_and_nonloopback_never_attempted(tmp_path):
    requests = []
    def refused(url, timeout):
        requests.append(url)
        raise urllib.error.URLError("connection refused")
    result = preflight(tmp_path, opener=refused)
    assert not result["ok"]
    assert requests == ["http://127.0.0.1:8080/api/status"]


def test_launch_command_has_no_root_sandbox_bypass_or_remote_url(tmp_path):
    cmd = kiosk.chromium_argv("/usr/bin/chromium", 8080,
                               profile=tmp_path / "isolated-profile")
    assert cmd[0] == "/usr/bin/chromium"
    assert "--kiosk" in cmd
    assert all("no-sandbox" not in value for value in cmd)
    assert cmd[-1] == "http://127.0.0.1:8080/"
    assert all("http:" not in value for value in cmd[:-1])
    with pytest.raises(ValueError):
        kiosk.chromium_argv("chromium", 8080, profile=tmp_path)


def test_bad_port_fails_before_probe(tmp_path):
    with pytest.raises(ValueError):
        preflight(tmp_path, port=0)
    with pytest.raises(ValueError):
        preflight(tmp_path, port=65536)


def test_cli_never_claims_touchscreen_was_hardware_verified(monkeypatch, capsys):
    report = {
        "ok": False, "checks": {"framebuffer": {"ok": False}},
        "physical_display_verified": False, "touch_verified": False,
    }
    monkeypatch.setattr(kiosk, "preflight", lambda *a, **k: report)
    assert kiosk.main(["--check", "--framebuffer", "/dev/fb1"]) == 2
    assert json.loads(capsys.readouterr().out) == report
    assert kiosk.main(["--launch", "--framebuffer", "/dev/fb1"]) == 2
    assert json.loads(capsys.readouterr().out) == report
