"""Live-radio supervisor tests run with no wireless hardware or Bettercap binary."""
import json
from pathlib import Path
import subprocess

import pytest

from redux.core import live_runtime as live
from redux.engine.bettercap_driver import Event
from redux.radio import Radio


def cfg(tmp_path, **changes):
    values = dict(state_dir=tmp_path / "state", capture_dir=tmp_path / "captures",
                  active_dir=tmp_path / "active",
                  enable_web=False, retry_seconds=3, probe_seconds=30)
    values.update(changes)
    return live.LiveConfig(**values)


def radio(iface="wlan1mon", *, monitor=True, onboard=False, bands=("2.4", "5")):
    return Radio(iface=iface, monitor=monitor, onboard=onboard,
                 bands=frozenset(bands), driver="mt76x2u")


def test_select_radio_only_capable_and_preferred():
    rs = [radio("wlan0", onboard=True, bands=("2.4",)),
          radio("wlan1mon")]
    assert live.select_radio(rs)[0] == "wlan1mon"
    assert live.select_radio(rs, preferred="wlan0")[0] == "wlan0"
    assert live.select_radio(rs, preferred="nope")[0] is None
    assert live.select_radio([radio(monitor=False)])[0] is None


def test_config_rejects_bad_paths_and_interfaces(tmp_path):
    with pytest.raises(ValueError, match="distinct"):
        cfg(tmp_path, web_port=8081).validate()
    with pytest.raises(ValueError, match="interface"):
        cfg(tmp_path, preferred_iface="foo;wifi.deauth").validate()
    with pytest.raises(ValueError, match="overlap"):
        cfg(tmp_path, state_dir=tmp_path / "captures" / "state").validate()
    with pytest.raises(ValueError, match="execut"):
        cfg(tmp_path, bettercap_binary="sh -c").validate()
    with pytest.raises(ValueError, match="rotation"):
        cfg(tmp_path, rotation_seconds=20).validate()
    cfg(tmp_path).validate()


def test_caplet_contains_only_passive_and_local_api(tmp_path):
    text = live.caplet_text("wlan1mon", tmp_path / "captures.pcap", "redux",
                            "abcd123456", 8081)
    assert "set wifi.handshakes.file" in text
    assert "set wifi.handshakes.aggregate true" in text
    assert "api.rest on" in text
    assert "127.0.0.1" in text
    assert "wifi.deauth" not in text
    assert "wifi.assoc" not in text
    assert "wifi.recon" not in text  # Augur starts it when REST is ready
    for bad in ["wlan0;wifi.deauth", "wlan0\nwifi.deauth"]:
        with pytest.raises(ValueError):
            live.caplet_text(bad, tmp_path / "captures.pcap", "redux", "abc", 8081)


def test_ensure_monitor_noop_for_already_monitor():
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "Interface wlan1mon\n  type monitor\n", "")
    assert live.ensure_monitor("wlan1mon", run=run) is False
    assert len(commands) == 1


def test_ensure_monitor_changes_only_selected_interface():
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        isinfo = len(args) > 3 and args[-1] == "info"
        out = "type managed" if isinfo and len(commands) == 1 else "type monitor"
        return subprocess.CompletedProcess(args, 0, out, "")
    assert live.ensure_monitor("wlan1mon", run=run) is True
    assert commands == [
        ["iw", "dev", "wlan1mon", "info"],
        ["ip", "link", "set", "wlan1mon", "down"],
        ["iw", "dev", "wlan1mon", "set", "type", "monitor"],
        ["ip", "link", "set", "wlan1mon", "up"],
        ["iw", "dev", "wlan1mon", "info"],
    ]


def test_ensure_monitor_errors_on_failed_transition():
    def runner(args, **kwargs):
        fail = args[:2] == ["ip", "link"]
        return subprocess.CompletedProcess(args, 1 if fail else 0, "type managed", "")
    with pytest.raises(RuntimeError, match="mode change"):
        live.ensure_monitor("wlan1mon", run=runner)


class Child:
    def __init__(self):
        self.pid = 12345
        self.dead = False
        self.terminated = False

    def poll(self):
        return 1 if self.dead else None

    def terminate(self):
        self.terminated = True
        self.dead = True

    def wait(self, timeout=None):
        self.dead = True
        return 0

    def kill(self):
        self.dead = True


class Transport:
    def __init__(self, config, timeout=1.5):
        self.config = config
        self.calls = []
        self.sample = [dict(tag="wifi.client.handshake",
                            data={"ap": "aa:bb:cc:dd:ee:ff"}, time=17.0),
                       dict(tag="wifi.ap.new",
                            data={"mac": "aa:bb:cc:dd:ee:ff", "essid": "lab",
                                  "channel": 6, "rssi": -42}, time=17.0)]
    def session(self):
        return {}
    def run(self, cmd):
        self.calls.append(cmd)
        return {}
    def events(self, clear=False):
        events = list(self.sample)
        if clear:
            self.sample.clear()
        return events


def test_driver_rejects_failed_engine_commands():
    class Broken:
        def run(self, command):
            return {"error": "simulated command error"}
        def events(self, clear=False):
            return []
    from redux.engine.bettercap_driver import BettercapConfig
    driver = live.LiveDriver(config=BettercapConfig(), transport=Broken())
    with pytest.raises(RuntimeError, match="interface selection"):
        driver.set_interface("wlan1mon")
    with pytest.raises(RuntimeError, match="passive recon"):
        driver.recon(True)


def test_single_live_owner_lock(tmp_path):
    first = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [])
    try:
        with pytest.raises(RuntimeError, match="already owns"):
            live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [])
    finally:
        first.close()
    with live_runtime_after_release(cfg(tmp_path)) as second:
        assert second.state == "starting"


class live_runtime_after_release:
    def __init__(self, config):
        self.config = config
        self.instance = None
    def __enter__(self):
        self.instance = live.LiveRuntime(self.config, radio_probe=lambda: [])
        return self.instance
    def __exit__(self, *_):
        self.instance.close()


def test_stale_capture_recovered_without_running_radio(tmp_path):
    import os
    import time
    settings = cfg(tmp_path)
    settings.active_dir.mkdir(parents=True)
    source = settings.active_dir / "bettercap-orphan.pcap"
    source.write_bytes(b"orphaned synthetic packet bytes")
    old = time.time() - 90
    os.utime(source, (old, old))
    runtime = live.LiveRuntime(settings, radio_probe=lambda: [], clock=lambda: 0)
    try:
        runtime.tick()  # no radio, but recovery must still make forward progress
        assert not source.exists()
        assert (settings.capture_dir / source.name).read_bytes() == b"orphaned synthetic packet bytes"
        assert runtime.handoffs == 1
    finally:
        runtime.close()


def test_fresh_active_capture_never_ingested_early(tmp_path):
    settings = cfg(tmp_path)
    settings.active_dir.mkdir(parents=True)
    source = settings.active_dir / "bettercap-active.pcap"
    source.write_bytes(b"synthetic currently open capture")
    runtime = live.LiveRuntime(settings, radio_probe=lambda: [], clock=lambda: 0)
    try:
        runtime.tick()
        assert source.is_file()
        assert not (settings.capture_dir / source.name).exists()
    finally:
        runtime.close()


def test_bad_handoff_source_and_collision_preserve_data(tmp_path):
    settings = cfg(tmp_path)
    runtime = live.LiveRuntime(settings, radio_probe=lambda: [])
    try:
        source = settings.active_dir / "bettercap-same.pcap"
        source.write_bytes(b"source bytes")
        target = settings.capture_dir / source.name
        target.write_bytes(b"keep me")
        assert runtime._handoff(source) is False
        assert source.read_bytes() == b"source bytes"
        assert target.read_bytes() == b"keep me"
        other = settings.active_dir / "bettercap-link.pcap"
        other.symlink_to(source)
        assert runtime._handoff(other) is False
    finally:
        runtime.close()


def test_no_radio_retries_without_spawning(tmp_path, monkeypatch):
    clock = [0]
    calls = []
    runtime = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [],
                               spawn=lambda *a, **k: calls.append(a),
                               clock=lambda: clock[0])
    assert runtime.tick() == "degraded"
    assert "no monitor-capable" in runtime.last_error
    assert runtime.tick() == "degraded"
    assert not calls
    assert json.loads((tmp_path / "state/live.json").read_text())["state"] == "degraded"
    runtime.close()


def test_engine_launch_augur_event_pump_restart_and_private_caplet(tmp_path, monkeypatch):
    children = []
    commands = []
    clock = [10.0]
    def spawn(argv, **kwargs):
        commands.append(argv)
        child = Child()
        children.append(child)
        return child
    def radio_command(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, "type monitor\n", "")
    monkeypatch.setattr(live, "HttpTransport", Transport)
    runtime = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [radio()],
                               executor=radio_command, spawn=spawn,
                               clock=lambda: clock[0])
    try:
        assert runtime.tick() == "starting_engine"
        assert commands and commands[0][0] == "bettercap"
        assert "-caplet" in commands[0]
        initial_capture = runtime.capture_file
        assert initial_capture.parent == tmp_path / "active"
        initial_capture.write_bytes(b"synthetic closed capture bytes")
        assert not list((tmp_path / "captures").glob("*.pcap"))
        caplet = tmp_path / "state/bettercap-live.cap"
        assert caplet.is_file() and not (caplet.stat().st_mode & 0o077)
        assert "set api.rest.password" in caplet.read_text()
        assert runtime.tick() == "running"
        assert "wifi.recon on" in runtime.transport.calls
        assert runtime.tick() == "running"
        assert runtime.created == 2
        assert runtime.handshakes == 1
        assert runtime.augur.store.count() == 1
        assert (tmp_path / "state/sightings.db").is_file()
        children[0].dead = True
        clock[0] += 1
        assert runtime.tick() == "degraded"
        assert (tmp_path / "captures" / initial_capture.name).read_bytes() == b"synthetic closed capture bytes"
        assert not initial_capture.exists()
        clock[0] += 3
        assert runtime.tick() == "starting_engine"
        assert len(children) == 2
        assert runtime.capture_file != initial_capture
    finally:
        runtime.close()
    assert children[-1].terminated


def test_capture_rotation_closes_and_delivers_session(tmp_path, monkeypatch):
    clock = [50.0]
    children = []
    monkeypatch.setattr(live, "HttpTransport", Transport)
    settings = cfg(tmp_path, rotation_seconds=30)
    def fake_spawn(*args, **kwargs):
        child = Child()
        children.append(child)
        return child
    def monitor(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "type monitor\n", "")
    runtime = live.LiveRuntime(
        settings, radio_probe=lambda: [radio()],
        executor=monitor, spawn=fake_spawn, clock=lambda: clock[0])
    try:
        assert runtime.tick() == "starting_engine"
        opened = runtime.capture_file
        opened.write_bytes(b"synthetic aggregate capture 123")
        assert runtime.tick() == "running"
        clock[0] += 30
        assert runtime.tick() == "rotating"
        assert children[0].terminated
        assert not opened.exists()
        assert (settings.capture_dir / opened.name).read_bytes() == b"synthetic aggregate capture 123"
        assert runtime.handoffs == 1
        clock[0] += 1
        assert runtime.tick() == "starting_engine"
        assert runtime.capture_file != opened
    finally:
        runtime.close()


def test_source_tree_is_importable_and_service_opt_in(tmp_path):
    assert callable(live.main)
    live.LiveConfig.load
