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
    folder.mkdir(exist_ok=True)
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
        "x11_check": lambda display: {
            "ok": display == ":0", "reason": "fixture X11 socket",
        },
        "x11_geometry_check": lambda display: {
            "ok": display == ":0", "resolution": [480, 320],
            "reason": "fixture X11 root geometry",
        },
    }
    opts.update(changes)
    return kiosk.preflight("/dev/fb1", **opts)


def test_real_preflight_has_explicit_honest_boundaries(tmp_path):
    result = preflight(tmp_path)
    assert result["ok"] is True
    assert result["checks"]["framebuffer"]["resolution"] == [480, 320]
    assert result["checks"]["framebuffer"]["name"] == "fb_ili9486"
    assert result["checks"]["browser"]["executable"] == "/usr/bin/chromium"
    assert result["checks"]["x11"]["ok"] is True
    assert result["checks"]["x11_geometry"]["ok"] is True
    assert result["checks"]["x11_geometry"]["resolution"] == [480, 320]
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


def test_preflight_rejects_relative_browser_path(tmp_path):
    report = preflight(tmp_path, which=lambda name: "chromium" if name == "chromium" else None)
    assert not report["ok"]
    assert not report["checks"]["browser"]["ok"]
    assert report["checks"]["browser"]["executable"] is None


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
    b'{"doctor":null,"runtime":{}}',
    b'{"doctor":{"overall":"ok"}}',
    b'{"runtime":{},"doctor":{"overall":"totally fine"}}',
    b'{"runtime":{},"doctor":"ok"}',
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


def test_kiosk_launch_refuses_missing_ephemeral_profile_directory(
    tmp_path, monkeypatch, capsys
):
    verified = {
        "ok": True, "checks": {"browser": {"executable": "/usr/bin/chromium"}},
        "touch_verified": False, "physical_display_verified": False,
    }
    monkeypatch.setattr(kiosk, "preflight", lambda *a, **k: verified)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert kiosk.main(["--launch", "--framebuffer", "/dev/fb1"]) == 2
    assert "XDG_RUNTIME_DIR" in capsys.readouterr().err
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "missing"))
    assert kiosk.main(["--launch", "--framebuffer", "/dev/fb1"]) == 2


def test_verified_kiosk_execs_only_loopback_browser_as_current_user(
    tmp_path, monkeypatch
):
    verified = {
        "ok": True, "checks": {"browser": {"executable": "/usr/bin/chromium"}},
        "touch_verified": False, "physical_display_verified": False,
    }
    monkeypatch.setattr(kiosk, "preflight", lambda *a, **k: verified)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    launched = []
    def fake_exec(path, argv):
        launched.append((path, argv))
        raise RuntimeError("mock exec")
    monkeypatch.setattr(kiosk.os, "execv", fake_exec)
    with pytest.raises(RuntimeError, match="mock exec"):
        kiosk.main(["--launch", "--framebuffer", "/dev/fb1"])
    assert len(launched) == 1
    executable, args = launched[0]
    assert executable == "/usr/bin/chromium"
    assert args == kiosk.chromium_argv(
        executable, 8080, profile=tmp_path / "redux-chromium")
    assert all("no-sandbox" not in arg for arg in args)


def test_check_flag_does_not_exec_browser_even_when_all_probes_pass(
    monkeypatch, capsys
):
    verified = {"ok": True, "checks": {"browser": {"executable": "/usr/bin/chromium"}},
                "touch_verified": False, "physical_display_verified": False}
    monkeypatch.setattr(kiosk, "preflight", lambda *a, **k: verified)
    monkeypatch.setattr(kiosk.os, "execv", lambda *a: pytest.fail("unwanted exec"))
    assert kiosk.main(["--check", "--framebuffer", "/dev/fb1"]) == 0
    assert json.loads(capsys.readouterr().out) == verified

def test_supervisor_restarts_bounded_number_of_times(monkeypatch):
    import signal
    spawned = []
    class Child:
        def __init__(self):
            self.pid = 100 + len(spawned)
        def wait(self, timeout=None):
            return 1
        def poll(self):
            return 1
    def spawn(command, **kwargs):
        assert command == ["/usr/bin/chromium", "--kiosk"]
        child = Child()
        spawned.append(child)
        return child
    sleeps = []
    assert kiosk.supervise(["/usr/bin/chromium", "--kiosk"],
                           spawn=spawn, sleep=sleeps.append,
                           max_failures=3, restart_delay=2) == 1
    assert len(spawned) == 3
    assert sleeps == [2, 2]


def test_supervised_kiosk_requires_preflight_and_secure_runtime(tmp_path, monkeypatch, capsys):
    verified = {
        "ok": True, "checks": {"browser": {"executable": "/usr/bin/chromium"}},
        "physical_display_verified": False, "touch_verified": False,
    }
    monkeypatch.setattr(kiosk, "preflight", lambda *a, **k: verified)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(kiosk, "supervise", lambda args: calls.append(args) or 1)
    assert kiosk.main(["--supervise", "--framebuffer", "/dev/fb1"]) == 1
    assert len(calls) == 1
    assert calls[0][-1] == "http://127.0.0.1:8080/"
    calls.clear()
    (tmp_path / "redux-chromium").symlink_to(tmp_path)
    assert kiosk.main(["--supervise", "--framebuffer", "/dev/fb1"]) == 2
    assert not calls
    assert "symlinked" in capsys.readouterr().err


def test_supervisor_does_not_run_if_dashboard_preflight_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(kiosk, "preflight", lambda *a, **k: {
        "ok": False, "checks": {}, "touch_verified": False,
        "physical_display_verified": False,
    })
    monkeypatch.setattr(kiosk, "supervise", lambda args: pytest.fail("unexpected browser"))
    assert kiosk.main(["--supervise", "--framebuffer", "/dev/fb1"]) == 2


def test_runtime_directory_must_be_private_and_owned(tmp_path, monkeypatch, capsys):
    verified = {"ok": True, "checks": {"browser": {"executable": "/usr/bin/chromium"}}}
    monkeypatch.setattr(kiosk, "preflight", lambda *a, **k: verified)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(kiosk.os, "execv", lambda *args: pytest.fail("unsafe exec"))
    old = tmp_path.stat().st_mode
    try:
        tmp_path.chmod(0o777)
        assert kiosk.main(["--launch", "--framebuffer", "/dev/fb1"]) == 2
        assert "Unsafe XDG_RUNTIME_DIR" in capsys.readouterr().err
    finally:
        tmp_path.chmod(old)


def test_browser_clean_exit_does_not_restart_or_enter_crash_loop():
    children = []
    class Browser:
        def wait(self, timeout=None):
            return 0
        def poll(self):
            return 0
    def spawn(*args, **kwargs):
        children.append(Browser())
        return children[-1]
    assert kiosk.supervise(["/usr/bin/chromium"], spawn=spawn,
                           sleep=lambda _: pytest.fail("unnecessary restart delay")) == 0
    assert len(children) == 1


def test_supervisor_signal_stops_unresponsive_browser_with_bounded_kill():
    import signal
    old_handler = signal.getsignal(signal.SIGTERM)
    child_events = []
    class Browser:
        dead = False
        def wait(self, timeout=None):
            child_events.append(("wait", timeout))
            if timeout == 0.5:
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                raise kiosk.subprocess.TimeoutExpired("browser", timeout)
            if timeout == 3:
                raise kiosk.subprocess.TimeoutExpired("browser", timeout)
            assert timeout == 2 and self.dead
            return -9
        def poll(self):
            return -9 if self.dead else None
        def terminate(self):
            child_events.append(("terminate", None))
        def kill(self):
            child_events.append(("kill", None))
            self.dead = True
    assert kiosk.supervise(["/usr/bin/chromium"], spawn=lambda *_a, **_k: Browser(),
                           sleep=lambda _: pytest.fail("no restart on signal")) == 0
    assert ("terminate", None) in child_events
    assert ("kill", None) in child_events
    assert ("wait", 2) in child_events
    assert signal.getsignal(signal.SIGTERM) is old_handler


def test_supervisor_rejects_bad_restart_policy_and_handles_spawn_failure(capsys):
    with pytest.raises(ValueError, match="restart policy"):
        kiosk.supervise(["/usr/bin/chromium"], max_failures=0)
    with pytest.raises(ValueError, match="absolute executable"):
        kiosk.supervise(["chromium"])
    assert kiosk.supervise(["/usr/bin/chromium"],
                           spawn=lambda *_a, **_k: (_ for _ in ()).throw(
                               FileNotFoundError("chromium missing"))) == 1
    assert "kiosk spawn failed" in capsys.readouterr().err

def test_x11_probe_requires_live_local_socket_and_rejects_remote_hosts(tmp_path):
    import socket
    for display in ("", "localhost:0", "host.example:0", "unix:0", ":abc",
                    ":1000", ":1;evil", ":1.300"):
        response = kiosk.x11_probe(display, socket_dir=tmp_path)
        assert not response["ok"]
        assert "local" in response["reason"] or "DISPLAY" in response["reason"]
    unix = tmp_path / "X2"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(unix))
        server.listen(2)
        assert kiosk.x11_probe(":2", socket_dir=tmp_path)["ok"]
        assert kiosk.x11_probe(":02.0", socket_dir=tmp_path)["ok"]
        assert not kiosk.x11_probe(":3", socket_dir=tmp_path)["ok"]
        link = tmp_path / "X3"
        link.symlink_to(unix)
        assert not kiosk.x11_probe(":3", socket_dir=tmp_path)["ok"]


def test_x11_preflight_requires_socket_and_cannot_claim_touch_proven(tmp_path):
    result = preflight(tmp_path, x11_check=lambda _: {
        "ok": False, "reason": "X11 connection refused",
    })
    assert not result["ok"]
    assert not result["checks"]["x11"]["ok"]
    assert result["touch_verified"] is False
    assert result["physical_display_verified"] is False


def test_symlinked_xdg_runtime_dir_fails_closed(tmp_path, monkeypatch, capsys):
    good = tmp_path / "real-runtime"
    good.mkdir(mode=0o700)
    shortcut = tmp_path / "linked-runtime"
    shortcut.symlink_to(good)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(shortcut))
    verified = {"ok": True, "checks": {"browser": {"executable": "/usr/bin/chromium"}}}
    monkeypatch.setattr(kiosk, "preflight", lambda *a, **k: verified)
    monkeypatch.setattr(kiosk.os, "execv", lambda *_a: pytest.fail("unsafe browser spawn"))
    assert kiosk.main(["--launch", "--framebuffer", "/dev/fb1"]) == 2
    assert "symlink" in capsys.readouterr().err

def test_x11_geometry_probe_accepts_tft_and_rejects_hdmi(tmp_path):
    import subprocess
    calls = []
    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, 0, "screen #0:\\n  dimensions:    480x320 pixels (120x80 millimeters)\\n", "",
        )
    probe = kiosk.x11_geometry_probe(
        ":0", which=lambda name: "/usr/bin/xdpyinfo", runner=runner)
    assert probe["ok"]
    assert probe["resolution"] == [480, 320]
    assert calls[0][0] == ["/usr/bin/xdpyinfo", "-display", ":0"]
    assert calls[0][1]["timeout"] == 3
    assert calls[0][1]["capture_output"] is True
    bad = kiosk.x11_geometry_probe(
        ":0", which=lambda _: "/usr/bin/xdpyinfo",
        runner=lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, "  dimensions: 1920x1080 pixels", ""),
    )
    assert not bad["ok"]
    assert bad["resolution"] == [1920, 1080]
    assert "not 480x320" in bad["reason"]


def test_x11_geometry_probe_requires_auth_and_installed_tool():
    import subprocess
    noauth = kiosk.x11_geometry_probe(
        ":0", which=lambda _: "/usr/bin/xdpyinfo",
        runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "auth denied"),
    )
    assert not noauth["ok"]
    assert "could not read" in noauth["reason"]
    no_tool = kiosk.x11_geometry_probe(":0", which=lambda _: None)
    assert not no_tool["ok"]
    assert "xdpyinfo" in no_tool["reason"]
    remote = kiosk.x11_geometry_probe(
        "remote:0", which=lambda _: "/usr/bin/xdpyinfo",
        runner=lambda *_a, **_k: pytest.fail("must not contact a remote X server"),
    )
    assert not remote["ok"]


def test_kiosk_preflight_rejects_hdmi_sized_x11_even_with_valid_fb(tmp_path):
    bad = preflight(tmp_path, x11_geometry_check=lambda display: {
        "ok": False, "resolution": [1920, 1080], "reason": "HDMI desktop",
    })
    assert not bad["ok"]
    assert bad["checks"]["framebuffer"]["ok"]
    assert bad["checks"]["x11"]["ok"]
    assert not bad["checks"]["x11_geometry"]["ok"]
    assert not bad["physical_display_verified"]
