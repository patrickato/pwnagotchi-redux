"""Live-radio supervisor tests run with no wireless hardware or Bettercap binary."""
import json
import os
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
    with pytest.raises(ValueError, match="boolean"):
        cfg(tmp_path, allow_connected_capture="yes").validate()
    cfg(tmp_path).validate()


def test_caplet_contains_only_passive_and_local_api(tmp_path):
    text = live.caplet_text("wlan1mon", tmp_path / "captures.pcap", "redux",
                            "abcd123456", 8081)
    assert "set wifi.handshakes.file" in text
    assert "set wifi.txpower 0" in text
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
        ["iw", "dev", "wlan1mon", "link"],
        ["ip", "link", "set", "wlan1mon", "down"],
        ["iw", "dev", "wlan1mon", "set", "type", "monitor"],
        ["ip", "link", "set", "wlan1mon", "up"],
        ["iw", "dev", "wlan1mon", "info"],
    ]



def test_safe_radio_selection_skips_connected_best_adapter():
    devices = [radio("wlan0", bands=("2.4",)),
               radio("wlan1", bands=("2.4", "5"))]
    calls = []
    def runner(args, **kwargs):
        calls.append(args)
        iface = args[2]
        if args[-1] == "info":
            return subprocess.CompletedProcess(args, 0,
                                               "type monitor\n" if iface == "wlan0"
                                               else "type managed\n", "")
        if args[-1] == "link":
            return subprocess.CompletedProcess(
                args, 0, "Connected to aa:bb:cc:dd:ee:ff\n", "")
        raise AssertionError("radio selection must not change modes")
    assert live.choose_safe_radio(devices, run=runner) == "wlan0"
    assert live.choose_safe_radio(devices, preferred="wlan1", run=runner) is None
    assert live.choose_safe_radio(devices, run=runner,
                                  allow_connected=True) == "wlan1"
    assert all(x[0] == "iw" for x in calls)


def test_safe_radio_selection_fails_closed_on_uninspectable_interface():
    def failing(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "cannot inspect")
    assert live.choose_safe_radio([radio()], run=failing) is None


def test_connected_wifi_uplink_does_not_get_disconnected():
    commands = []
    def runner(argv, **kwargs):
        commands.append(argv)
        if argv[-1] == "info":
            return subprocess.CompletedProcess(argv, 0, "type managed\n", "")
        if argv[-1] == "link":
            return subprocess.CompletedProcess(argv, 0,
                                               "Connected to aa:bb:cc:dd:ee:ff\n", "")
        raise AssertionError("must never take an active uplink down")
    with pytest.raises(RuntimeError, match="connected uplink"):
        live.ensure_monitor("wlan0", run=runner)
    assert commands == [
        ["iw", "dev", "wlan0", "info"],
        ["iw", "dev", "wlan0", "link"],
    ]


def test_connected_wifi_can_be_reassigned_only_with_explicit_override():
    commands = []
    def runner(argv, **kwargs):
        commands.append(argv)
        if argv == ["iw", "dev", "wlan0", "info"]:
            infos = sum(x == argv for x in commands)
            return subprocess.CompletedProcess(argv, 0,
                "type managed\n" if infos == 1 else "type monitor\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")
    assert live.ensure_monitor("wlan0", run=runner, allow_connected=True) is True
    assert ["iw", "dev", "wlan0", "link"] not in commands
    assert ["ip", "link", "set", "wlan0", "down"] in commands


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


def test_readonly_preflight_reports_real_requirements(tmp_path):
    conf = cfg(tmp_path)
    outcome = live.preflight(
        conf, which=lambda executable: "/usr/bin/" + executable,
        radio_probe=lambda: [radio()],
        radio_run=lambda args, **kw: subprocess.CompletedProcess(args, 0, "type monitor\n", ""),
        available_bytes=lambda path: 256 * 1024 * 1024)
    assert outcome["ready"] is True
    assert outcome["capture_radio"] == "wlan1mon"
    assert outcome["checks"]["converter"] is True
    # It only observes directories; it never creates or modifies them.
    assert not conf.active_dir.exists()
    assert not conf.state_dir.exists()


def test_readonly_preflight_fails_missing_tools_radio_and_storage(tmp_path):
    conf = cfg(tmp_path)
    outcome = live.preflight(
        conf, which=lambda executable: None, radio_probe=lambda: [],
        available_bytes=lambda path: 1024)
    assert not outcome["ready"]
    assert outcome["capture_radio"] is None
    assert any("capture storage below" in e for e in outcome["errors"])
    assert any("no safe monitor-capable" in e for e in outcome["errors"])
    assert any("bettercap executable" in e for e in outcome["errors"])


def test_readonly_preflight_checks_writable_partition_mount():
    conf = live.LiveConfig()
    outcome = live.preflight(
        conf, which=lambda tool: tool, radio_probe=lambda: [radio()],
        radio_run=lambda args, **kw: subprocess.CompletedProcess(args, 0, "type monitor\n", ""),
        is_mount=lambda path: False,
        available_bytes=lambda path: 512 * 1024 * 1024)
    assert not outcome["ready"]
    assert any("REDUXCAP" in e for e in outcome["errors"])
    assert outcome["checks"]["capture_mount"] is False


def test_driver_refuses_rest_success_false_without_executing_recon():
    class Rejected:
        def run(self, command):
            return {"success": False, "msg": "synthetic denied operation"}
        def events(self, clear=False):
            return []
    from redux.engine.bettercap_driver import BettercapConfig
    driver = live.LiveDriver(config=BettercapConfig(), transport=Rejected())
    with pytest.raises(RuntimeError, match="interface selection"):
        driver.set_interface("wlan1mon")
    with pytest.raises(RuntimeError, match="capture output configuration"):
        driver.set_handshake_file("/captures/active/synthetic.pcap")
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


def test_low_storage_does_not_launch_engine(tmp_path, monkeypatch):
    clock = [0]
    launches = []
    monkeypatch.setattr(live, "free_bytes", lambda path: 1024)
    runtime = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [radio()],
                               spawn=lambda *a, **kw: launches.append(a),
                               clock=lambda: clock[0])
    try:
        assert runtime.tick() == "degraded"
        assert "free-space reserve" in runtime.last_error
        assert launches == []
    finally:
        runtime.close()


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


def test_abandoned_capture_directory_failure_is_retryable(tmp_path, monkeypatch):
    import os
    import time
    conf = cfg(tmp_path)
    source = conf.active_dir / "bettercap-recovery.pcap"
    conf.active_dir.mkdir(parents=True)
    source.write_bytes(b"old finished capture")
    old = time.time() - 120
    os.utime(source, (old, old))
    now = [0]
    runtime = live.LiveRuntime(conf, radio_probe=lambda: [], clock=lambda: now[0])
    original_iterdir = Path.iterdir

    def intermittent_scan(path):
        if path == conf.active_dir:
            raise OSError("synthetic SD directory I/O error")
        return original_iterdir(path)

    try:
        monkeypatch.setattr(Path, "iterdir", intermittent_scan)
        assert runtime.tick() == "degraded"
        assert source.is_file()
        assert "recovery scan failed" in runtime.last_handoff_error
        monkeypatch.setattr(Path, "iterdir", original_iterdir)
        now[0] += 20
        runtime.tick()
        assert not source.exists()
        assert (conf.capture_dir / source.name).read_bytes() == b"old finished capture"
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


def test_crash_between_queue_link_and_source_unlink_recovers(tmp_path):
    import os
    settings = cfg(tmp_path)
    runtime = live.LiveRuntime(settings, radio_probe=lambda: [])
    try:
        source = settings.active_dir / "bettercap-interrupted.pcap"
        queued = settings.capture_dir / source.name
        source.write_bytes(b"test payload with durable source")
        os.link(source, queued)  # interrupted publish before active name removed
        assert source.stat().st_ino == queued.stat().st_ino
        assert runtime._handoff(source) is True
        assert not source.exists()
        assert queued.read_bytes() == b"test payload with durable source"
    finally:
        runtime.close()


def test_failed_publication_leaves_only_original_capture(tmp_path, monkeypatch):
    settings = cfg(tmp_path)
    runtime = live.LiveRuntime(settings, radio_probe=lambda: [])
    try:
        source = settings.active_dir / "bettercap-pending.pcap"
        source.write_bytes(b"retriable capture")
        def refuse_link(*args, **kwargs):
            raise OSError("synthetic directory write failure")
        monkeypatch.setattr(live.os, "link", refuse_link)
        assert runtime._handoff(source) is False
        assert source.read_bytes() == b"retriable capture"
        assert not (settings.capture_dir / source.name).exists()
        assert "handoff failed" in runtime.last_handoff_error
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


def test_transient_database_error_retries_without_restarting_bettercap(tmp_path, monkeypatch):
    import sqlite3
    clock = [100.0]
    started = []
    def spawn(*args, **kwargs):
        child = Child()
        started.append(child)
        return child
    def iw(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "type monitor\n", "")
    monkeypatch.setattr(live, "HttpTransport", Transport)
    runtime = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [radio()],
                               executor=iw, spawn=spawn, clock=lambda: clock[0])
    try:
        assert runtime.tick() == "starting_engine"
        assert runtime.tick() == "running"
        real_insert = runtime.augur.store.insert_many
        attempts = [0]
        def intermittent(batch):
            attempts[0] += 1
            if attempts[0] == 1:
                raise sqlite3.OperationalError("synthetic database temporarily busy")
            return real_insert(batch)
        runtime.augur.store.insert_many = intermittent
        assert runtime.tick() == "telemetry_degraded"
        assert len(started) == 1 and not started[0].terminated
        assert len(runtime.augur._sighting_buffer) == 1
        assert runtime._snapshot["runtime"]["sightings_pending"] == 1
        assert runtime._snapshot["doctor"]["overall"] == "degraded"
        clock[0] += 1
        assert runtime.tick() == "telemetry_degraded"
        assert attempts[0] == 1  # no repeated rapid failed commits
        clock[0] += runtime.config.retry_seconds
        assert runtime.tick() == "running"
        assert attempts[0] >= 2
        assert runtime.augur.store.count() == 1
        assert runtime.augur._sighting_buffer == []
        assert runtime.sighting_loss_events == 0
        assert len(started) == 1 and not started[0].terminated
    finally:
        runtime.close()


def test_unrecoverable_database_write_reports_uncommitted_count(tmp_path, monkeypatch):
    import sqlite3
    clock = [110.0]
    def spawn(*args, **kwargs):
        return Child()
    def iw(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "type monitor\n", "")
    monkeypatch.setattr(live, "HttpTransport", Transport)
    runtime = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [radio()],
                               executor=iw, spawn=spawn, clock=lambda: clock[0])
    try:
        runtime.tick()
        assert runtime.tick() == "running"
        def cannot_write(batch):
            raise sqlite3.OperationalError("synthetic read-only database")
        runtime.augur.store.insert_many = cannot_write
        assert runtime.tick() == "telemetry_degraded"
        assert len(runtime.augur._sighting_buffer) == 1
    finally:
        runtime.close()
    assert runtime.sighting_loss_events == 1
    assert runtime._snapshot["runtime"]["sightings_lost_on_restart"] == 1
    assert runtime._snapshot["doctor"]["overall"] == "action"
    area = next(item for item in runtime._snapshot["doctor"]["findings"]
                if item["area"] == "sighting persistence")
    assert area["status"] == "action"


def test_live_status_reuses_one_processing_ledger_read(tmp_path, monkeypatch):
    from redux.crack import ingest
    def spawn(*args, **kwargs):
        return Child()
    def monitor(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "type monitor\n", "")
    monkeypatch.setattr(live, "HttpTransport", Transport)
    runtime = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [radio()],
                               executor=monitor, spawn=spawn)
    try:
        assert runtime.tick() == "starting_engine"
        assert runtime.tick() == "running"
        expected = {"available": True, "artifacts": 12, "hash_records": 3,
                    "by_status": {"ready": 3}, "last_scan": {
                        "completed_utc": 1000.0, "scanned": 2,
                        "outcomes": {"ready": 1}}}
        calls = []
        def only_ledger_read(path):
            calls.append(path)
            return expected
        monkeypatch.setattr(live, "read_summary", only_ledger_read)
        monkeypatch.setattr(ingest, "read_summary",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("Augur duplicated the processing DB read")))
        runtime._checkpoint()
        assert len(calls) == 1
        assert calls[0] == runtime.config.capture_dir.parent / "jobs.db"
        assert runtime._snapshot["capture_processing"] is expected
    finally:
        runtime.close()


def test_heavy_snapshot_is_sampled_at_bounded_cadence_but_doctor_stays_live(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    clock = [100.0]
    runtime = live.LiveRuntime(cfg(tmp_path, status_sample_seconds=5),
                               radio_probe=lambda: [], clock=lambda: clock[0])
    class FakeAugur:
        def __init__(self):
            self.doctor_calls = 0
        def doctor_report(self):
            self.doctor_calls += 1
            return {"findings": []}
        def flush_sightings(self):
            return 0
        _sighting_buffer = []
        _sighting_flush_error = ""
        _sighting_flush_failures = 0
        store = SimpleNamespace(close=lambda: None)
    agent = FakeAugur()
    runtime.augur = agent
    sampled = []
    ledgers = []
    report = {"available": True, "artifacts": 0, "by_status": {},
              "last_scan": {"completed_utc": 1000, "scanned": 0, "outcomes": {}}}
    def heavy(augur, processing_report=None):
        sampled.append(clock[0])
        assert processing_report is report
        return {"sightings": len(sampled), "airspace": {"channels": {}}}
    def ledger(path):
        ledgers.append(clock[0])
        return report
    monkeypatch.setattr(live, "status_payload", heavy)
    monkeypatch.setattr(live, "read_summary", ledger)
    try:
        runtime._checkpoint()
        first = runtime._snapshot
        assert first["sightings"] == 1
        assert first["runtime"]["visual_sampled_utc"] is not None
        clock[0] = 102.0
        runtime._checkpoint()
        assert len(sampled) == 1 and len(ledgers) == 1
        assert agent.doctor_calls == 2
        assert runtime._snapshot is not first
        assert first["runtime"]["updated_utc"] == first["runtime"]["updated_utc"]
        clock[0] = 104.9
        runtime._checkpoint()
        assert len(sampled) == 1
        clock[0] = 105.0
        runtime._checkpoint()
        assert len(sampled) == 2 and len(ledgers) == 2
        # A newly seen real event invalidates the view immediately,
        # even when the interval has not yet elapsed.
        runtime._observe(SimpleNamespace(payload={"event": None}))
        clock[0] = 105.1
        runtime._checkpoint()
        assert len(sampled) == 3 and len(ledgers) == 2
        assert runtime._snapshot["runtime"]["events_seen"] == 1
        assert agent.doctor_calls == 5
    finally:
        runtime.close()


def test_live_snapshot_is_published_atomically_after_doctor_completion(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    runtime = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [])
    runtime._snapshot = {"runtime": {"state": "stable-old"}, "doctor": {"old": True}}
    old = runtime._snapshot
    class FakeAugur:
        _sighting_buffer = []
        _sighting_flush_error = ""
        _sighting_flush_failures = 0
        store = SimpleNamespace(close=lambda: None)
        def flush_sightings(self):
            return 0
        def doctor_report(self):
            # The HTTP thread should still see a full previous snapshot while
            # the store thread computes an updated health assessment.
            assert runtime._snapshot is old
            assert old["runtime"]["state"] == "stable-old"
            assert old["doctor"] == {"old": True}
            return {"findings": []}
    runtime.augur = FakeAugur()
    monkeypatch.setattr(live, "read_summary", lambda path: {
        "available": False, "reason": "not yet recorded"})
    monkeypatch.setattr(live, "status_payload",
                        lambda augur, processing_report=None: {"sightings": 3})
    try:
        runtime._checkpoint()
        assert runtime._snapshot is not old
        assert runtime._snapshot["runtime"]["state"] == "starting"
        assert runtime._snapshot["doctor"]["findings"]
        assert runtime._snapshot["sightings"] == 3
        assert old == {"runtime": {"state": "stable-old"}, "doctor": {"old": True}}
    finally:
        runtime.close()


def test_status_sample_config_rejects_invalid_limits(tmp_path):
    for invalid in (0, -1, 31, 999):
        with pytest.raises(ValueError, match="status_sample_seconds"):
            cfg(tmp_path, status_sample_seconds=invalid).validate()
    cfg(tmp_path, status_sample_seconds=1).validate()
    cfg(tmp_path, status_sample_seconds=30).validate()


def test_stuck_child_cannot_publish_capture_or_start_second_engine(tmp_path):
    clock = [10.0]
    class FlakyChild(Child):
        def __init__(self):
            super().__init__()
            self.attempts = 0
        def terminate(self):
            self.attempts += 1
            if self.attempts == 1:
                raise PermissionError("synthetic signal refused")
            return super().terminate()

    runtime = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [],
                               clock=lambda: clock[0])
    child = FlakyChild()
    runtime.process = child
    runtime.state = "running"
    original = runtime.config.active_dir / "capture-running.pcap"
    original.write_bytes(b"synthetic raw unfinished capture")
    runtime.capture_file = original
    try:
        assert runtime._drop() is False
        assert runtime.state == "termination_pending"
        assert runtime.process is child
        assert original.is_file()
        assert not list(runtime.config.capture_dir.glob("*.pcap"))
        assert "termination not confirmed" in runtime.last_error
        assert runtime.tick() == "degraded"
        assert child.dead is True
        assert child.attempts == 2
        assert runtime.process is None
        assert not original.exists()
        assert (runtime.config.capture_dir / original.name).read_bytes() == (
            b"synthetic raw unfinished capture"
        )
        assert runtime.tick() == "degraded"  # bounded retry, no new radio yet
    finally:
        runtime.close()


def test_child_wait_timeout_escalates_to_kill_before_capture_handoff(tmp_path):
    class IgnoresTerm(Child):
        def __init__(self):
            super().__init__()
            self.kill_called = False
        def terminate(self):
            self.terminated = True
        def wait(self, timeout=None):
            if not self.dead:
                raise subprocess.TimeoutExpired("bettercap", timeout)
            return 0
        def kill(self):
            self.kill_called = True
            self.dead = True

    runtime = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [])
    child = IgnoresTerm()
    runtime.process = child
    original = runtime.config.active_dir / "not-ready.pcap"
    original.write_bytes(b"captured sample")
    runtime.capture_file = original
    try:
        assert runtime._drop() is True
        assert child.terminated and child.kill_called
        assert (runtime.config.capture_dir / original.name).read_bytes() == (
            b"captured sample"
        )
    finally:
        runtime.close()


def test_unreapable_child_fails_supervisor_shutdown_without_handoff(tmp_path):
    class StubbornChild(Child):
        def terminate(self):
            self.terminated = True  # pretend TERM was delivered but child survives
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("bettercap", timeout)
        def kill(self):
            self.dead = False  # simulated SIGKILL failed to confirm exit

    runtime = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [])
    child = StubbornChild()
    runtime.process = child
    original = runtime.config.active_dir / "still-writing.pcap"
    original.write_bytes(b"unfinished")
    runtime.capture_file = original
    with pytest.raises(RuntimeError, match="remained alive"):
        runtime.close()
    assert runtime.process is child
    assert runtime.state == "termination_pending"
    assert original.read_bytes() == b"unfinished"
    assert not list(runtime.config.capture_dir.glob("*.pcap"))
    # The per-process owner lock must have been released on service exit.
    reopened = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [])
    reopened.close()


def test_terminated_child_is_reaped_before_capture_publication(tmp_path):
    class ExitedChild(Child):
        def __init__(self):
            super().__init__()
            self.dead = True
            self.wait_calls = []
        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            return 17
        def terminate(self):
            raise AssertionError("already-exited process must not be signalled")

    runtime = live.LiveRuntime(cfg(tmp_path), radio_probe=lambda: [])
    child = ExitedChild()
    runtime.process = child
    source = runtime.config.active_dir / "already-done.pcap"
    source.write_bytes(b"closed")
    runtime.capture_file = source
    try:
        assert runtime._drop() is True
        assert child.wait_calls == [0]
        assert (runtime.config.capture_dir / source.name).read_bytes() == b"closed"
    finally:
        runtime.close()


def test_rotation_retries_child_stop_without_publishing_or_duplicate_engine(
    tmp_path, monkeypatch
):
    clock = [50.0]
    children = []
    class TemporarilyUnstoppable(Child):
        def __init__(self):
            super().__init__()
            self.stop_calls = 0
        def terminate(self):
            self.stop_calls += 1
            if self.stop_calls == 1:
                raise PermissionError("synthetic transient SIGTERM failure")
            return super().terminate()
    def spawn(*args, **kwargs):
        # Only the first engine simulates a transient stop fault. The newly
        # started engine must remain normally stoppable by fixture teardown.
        child = TemporarilyUnstoppable() if not children else Child()
        children.append(child)
        return child
    def monitor(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "type monitor\n", "")
    monkeypatch.setattr(live, "HttpTransport", Transport)
    runtime = live.LiveRuntime(
        cfg(tmp_path, rotation_seconds=30), radio_probe=lambda: [radio()],
        executor=monitor, spawn=spawn, clock=lambda: clock[0],
    )
    try:
        assert runtime.tick() == "starting_engine"
        assert runtime.tick() == "running"
        source = runtime.capture_file
        source.write_bytes(b"capture writer must exit first")
        clock[0] += 30
        assert runtime.tick() == "termination_pending"
        assert runtime.process is children[0]
        assert source.exists()
        assert not list(runtime.config.capture_dir.glob("*.pcap"))
        assert len(children) == 1
        clock[0] += 1
        assert runtime.tick() == "degraded"
        assert children[0].dead is True
        assert (runtime.config.capture_dir / source.name).read_bytes() == (
            b"capture writer must exit first"
        )
        assert len(children) == 1
        clock[0] += runtime.config.retry_seconds
        assert runtime.tick() == "starting_engine"
        assert len(children) == 2
    finally:
        runtime.close()


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


def test_status_disk_write_error_does_not_kill_runtime(tmp_path, monkeypatch):
    conf = cfg(tmp_path)
    runtime = live.LiveRuntime(conf, radio_probe=lambda: [], clock=lambda: 1)
    try:
        def disk_failed(*args, **kwargs):
            raise OSError("synthetic read-only SD card")
        monkeypatch.setattr(live, "atomic_checkpoint", disk_failed)
        assert runtime.tick() == "degraded"
        assert "persistence failed" in runtime.last_error
        assert runtime._snapshot["runtime"]["state"] == "degraded"
        assert runtime.tick() == "degraded"
    finally:
        runtime.close()


def test_metrics_storage_probe_failure_preserves_degraded_status(tmp_path, monkeypatch):
    conf = cfg(tmp_path)
    runtime = live.LiveRuntime(conf, radio_probe=lambda: [], clock=lambda: 0)
    try:
        def missing_storage(*args, **kwargs):
            raise OSError("synthetic ejected storage")
        monkeypatch.setattr(live, "free_bytes", missing_storage)
        assert runtime.tick() == "degraded"
        assert runtime._snapshot["runtime"]["free_bytes"] is None
        assert "synthetic ejected storage" in runtime.last_error
    finally:
        runtime.close()


def test_live_cli_reads_real_snapshot_without_stub_radios(tmp_path, capsys):
    from redux.cli import main
    file = tmp_path / "live.json"
    data = {"state": "running", "iface": "wlan1mon", "handshake_events": 3}
    file.write_text(json.dumps(data))
    assert main(["live", "status", "--file", str(file)]) == 0
    stdout = capsys.readouterr().out
    assert json.loads(stdout) == data
    file.unlink()
    assert main(["live", "status", "--file", str(file)]) == 3
    file.symlink_to(tmp_path / "secret")
    assert main(["live", "status", "--file", str(file)]) == 3
    file.unlink()
    file.write_text("invalid json")
    assert main(["live", "status", "--file", str(file)]) == 4


def test_local_doctor_dashboard_starts_even_when_capture_radio_is_missing(tmp_path):
    import socket
    import urllib.request
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = cfg(tmp_path, enable_web=True, web_port=port)
    runtime = live.LiveRuntime(settings, radio_probe=lambda: [])
    try:
        assert runtime.tick() == "degraded"
        assert runtime.web is not None
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status",
                                    timeout=3) as reply:
            snapshot = json.load(reply)
        assert snapshot["runtime"]["state"] == "degraded"
        assert snapshot["runtime"]["dashboard_active"] is True
        assert snapshot["doctor"]["overall"] == "degraded"
        display = next(x for x in snapshot["doctor"]["findings"]
                       if x["area"] == "local dashboard")
        assert display["status"] == "ok"
        assert "capture radio" in snapshot["doctor"]["coverage"]["not_assessed"]
    finally:
        runtime.close()


def test_dashboard_binding_errors_are_reported_and_retry_is_bounded(tmp_path, monkeypatch):
    now = [0.0]
    attempts = []
    def occupied(*args, **kwargs):
        attempts.append(args)
        raise OSError("port already in use")
    monkeypatch.setattr(live, "ThreadingHTTPServer", occupied)
    settings = cfg(tmp_path, enable_web=True)
    runtime = live.LiveRuntime(settings, radio_probe=lambda: [], clock=lambda: now[0])
    try:
        assert runtime.tick() == "degraded"
        assert len(attempts) == 1
        assert runtime._snapshot["runtime"]["dashboard_active"] is False
        finding = next(x for x in runtime._snapshot["doctor"]["findings"]
                       if x["area"] == "local dashboard")
        assert finding["status"] == "degraded"
        assert "startup failed" in finding["reason"]
        assert runtime.tick() == "degraded"
        assert len(attempts) == 1  # no log storm on every supervisor tick
        now[0] = 30.0
        assert runtime.tick() == "degraded"
        assert len(attempts) == 2
    finally:
        runtime.close()


def test_dashboard_thread_start_failure_cleans_socket_and_reports_issue(tmp_path, monkeypatch):
    servers = []
    class Server:
        def __init__(self, *args, **kwargs):
            self.closed = False
            servers.append(self)
        def serve_forever(self):
            pass
        def server_close(self):
            self.closed = True
    class BrokenThread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            raise RuntimeError("synthetic thread startup failure")
    monkeypatch.setattr(live, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(live.threading, "Thread", BrokenThread)
    runtime = live.LiveRuntime(cfg(tmp_path, enable_web=True),
                               radio_probe=lambda: [], clock=lambda: 0)
    try:
        assert runtime.tick() == "degraded"
        assert runtime.web is None
        assert servers[0].closed
        assert "dashboard startup failed" in runtime.web_error
        finding = next(x for x in runtime._snapshot["doctor"]["findings"]
                       if x["area"] == "local dashboard")
        assert finding["status"] == "degraded"
    finally:
        runtime.close()


def test_dead_dashboard_thread_is_not_reported_as_healthy(tmp_path, monkeypatch):
    state = {"threads": []}
    class Server:
        def __init__(self, *args, **kwargs):
            self.closed = False
        def serve_forever(self):
            pass
        def server_close(self):
            self.closed = True
    class FakeThread:
        def __init__(self, **kwargs):
            self.alive = False
            state["threads"].append(self)
        def start(self):
            self.alive = True
        def is_alive(self):
            return self.alive
    monkeypatch.setattr(live, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(live.threading, "Thread", FakeThread)
    now = [0]
    runtime = live.LiveRuntime(cfg(tmp_path, enable_web=True),
                               radio_probe=lambda: [], clock=lambda: now[0])
    try:
        assert runtime.tick() == "degraded"
        server = runtime.web
        assert server is not None
        state["threads"][0].alive = False
        now[0] = 1
        assert runtime.tick() == "degraded"
        assert server.closed is True
        assert runtime.web is None
        assert "stopped unexpectedly" in runtime.web_error
        assert runtime._snapshot["runtime"]["dashboard_active"] is False
        assert runtime.web_next_try == 31
    finally:
        runtime.close()


def test_live_bettercap_log_is_bounded_without_engine_restart(tmp_path, monkeypatch):
    import fcntl
    from redux.core import live_runtime as live
    started = []
    def fake_spawn(argv, **kwargs):
        assert kwargs["stdout"] is not None
        started.append(kwargs["stdout"])
        return Child()
    def monitor(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "type monitor\n", "")
    settings = cfg(tmp_path, max_log_bytes=65536)
    runtime = live.LiveRuntime(settings, radio_probe=lambda: [radio()],
                               executor=monitor, spawn=fake_spawn)
    try:
        assert runtime.tick() == "starting_engine"
        assert len(started) == 1
        assert fcntl.fcntl(runtime.log_handle.fileno(), fcntl.F_GETFL) & os.O_APPEND
        runtime.log_handle.write(b"x" * 65536)
        assert (settings.state_dir / "bettercap.log").stat().st_size == 65536
        runtime._enforce_log_limit()
        assert runtime.log_truncations == 1
        assert (settings.state_dir / "bettercap.log").stat().st_size == 0
        runtime.log_handle.write(b"after rollover\n")
        assert (settings.state_dir / "bettercap.log").read_bytes() == b"after rollover\n"
        assert runtime.process is not None
        assert not runtime.process.terminated  # live Bettercap remains running
        runtime._checkpoint()
        snap = runtime._snapshot["runtime"]
        assert snap["log_bytes"] == len(b"after rollover\n")
        assert snap["log_truncations"] == 1
        assert snap["log_error"] == ""
    finally:
        runtime.close()


def test_live_log_symlink_and_hardlink_rejected(tmp_path):
    import os
    settings = cfg(tmp_path)
    runtime = live.LiveRuntime(settings, radio_probe=lambda: [])
    protected = tmp_path / "protected"
    protected.write_bytes(b"do not change")
    path = settings.state_dir / "bettercap.log"
    archive = settings.state_dir / "bettercap.log.previous"
    try:
        path.symlink_to(protected)
        with pytest.raises(OSError):
            runtime._open_engine_log()
        assert protected.read_bytes() == b"do not change"
        path.unlink()
        os.link(protected, path)
        with pytest.raises(ValueError, match="linked"):
            runtime._open_engine_log()
        assert protected.read_bytes() == b"do not change"
        path.unlink()
        archive.symlink_to(protected)
        with pytest.raises(ValueError, match="archive"):
            runtime._open_engine_log()
        assert protected.read_bytes() == b"do not change"
    finally:
        runtime.close()


def test_log_size_enforcement_errors_do_not_kill_capture_owner(tmp_path, monkeypatch):
    import os
    spawned = []
    def spawn(*args, **kw):
        proc = Child()
        spawned.append(proc)
        return proc
    def monitor(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "type monitor\n", "")
    settings = cfg(tmp_path, max_log_bytes=65536)
    runtime = live.LiveRuntime(settings, radio_probe=lambda: [radio()],
                               executor=monitor, spawn=spawn)
    try:
        assert runtime.tick() == "starting_engine"
        runtime.log_handle.write(b"x" * 65536)
        real_truncate = os.ftruncate
        def refuse_truncate(fd, length):
            if fd == runtime.log_handle.fileno():
                raise OSError("synthetic media failure")
            return real_truncate(fd, length)
        monkeypatch.setattr(live.os, "ftruncate", refuse_truncate)
        runtime._enforce_log_limit()
        assert "enforcement failed" in runtime.log_error
        assert runtime.process is spawned[0] and not spawned[0].terminated
        runtime._checkpoint()
        assert runtime._snapshot["runtime"]["log_error"]
        finding = next(f for f in runtime._snapshot["doctor"]["findings"]
                       if f["area"] == "engine logging")
        assert finding["status"] == "degraded"
        monkeypatch.setattr(live.os, "ftruncate", real_truncate)
        runtime._enforce_log_limit()
        assert runtime.log_error == ""
        assert runtime.log_truncations == 1
    finally:
        runtime.close()


def test_live_log_recovers_oversized_prior_image_log(tmp_path):
    settings = cfg(tmp_path, max_log_bytes=65536)
    runtime = live.LiveRuntime(settings, radio_probe=lambda: [])
    path = settings.state_dir / "bettercap.log"
    archive = settings.state_dir / "bettercap.log.previous"
    path.write_bytes(b"x" * 100000)
    archive.write_bytes(b"y" * 100000)
    try:
        handle = runtime._open_engine_log()
        runtime.log_handle = handle
        assert path.stat().st_size == 0
        assert archive.stat().st_size == 0
        assert runtime.log_truncations == 1
        assert path.stat().st_mode & 0o077 == 0
    finally:
        runtime.close()


def test_log_budget_config_enforced(tmp_path):
    for val in (0, -1, 1024, 64 * 1024 * 1024 + 1):
        with pytest.raises(ValueError, match="max_log_bytes"):
            cfg(tmp_path, max_log_bytes=val).validate()
    cfg(tmp_path, max_log_bytes=65536).validate()


def test_missing_production_capture_mount_rejected_before_any_directory_creation(monkeypatch):
    from pathlib import Path
    requested = []
    def forbidden_mkdir(self, *args, **kwargs):
        requested.append(str(self))
        raise AssertionError("must not create rootfs fallback capture directories")
    monkeypatch.setattr(live.os.path, "ismount", lambda path: False)
    monkeypatch.setattr(Path, "mkdir", forbidden_mkdir)
    settings = live.LiveConfig(
        state_dir=Path("/captures/redux"),
        capture_dir=Path("/captures/incoming"),
        active_dir=Path("/captures/active"),
    )
    with pytest.raises(RuntimeError, match="not mounted"):
        live.LiveRuntime(settings, radio_probe=lambda: [])
    assert requested == []


def test_runtime_mount_loss_stops_capture_without_rootfs_fallback_or_auto_resume(
    tmp_path, monkeypatch
):
    children = []
    def spawn(*args, **kwargs):
        child = Child()
        children.append(child)
        return child
    def monitor(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "type monitor\n", "")
    from redux.core import live_runtime as live
    settings = cfg(tmp_path)
    runtime = live.LiveRuntime(settings, radio_probe=lambda: [radio()],
                               executor=monitor, spawn=spawn)
    mounted = [True]
    runtime.capture_mount_required = True
    runtime._capture_mount_ok = lambda: mounted[0]
    try:
        assert runtime.tick() == "starting_engine"
        assert len(children) == 1
        capture_path = runtime.capture_file
        capture_path.write_bytes(b"preserve stale capture while partition is offline")
        saved_before = (settings.state_dir / "live.json").read_bytes()
        mounted[0] = False
        assert runtime.tick() == "storage_paused"
        assert children[0].terminated
        assert runtime.process is None
        assert capture_path.exists()  # handoff must not write to an unmounted rootfs
        assert not list(settings.capture_dir.glob("*.pcap"))
        snap = runtime._snapshot["runtime"]
        assert snap["capture_mount"] is False
        assert snap["free_bytes"] is None
        assert snap["health"] == "action"
        assert "restart" in snap["last_error"]
        assert (settings.state_dir / "live.json").read_bytes() == saved_before
        assert "REDUXCAP" in runtime.last_handoff_error

        mounted[0] = True
        assert runtime.tick() == "storage_paused"
        assert len(children) == 1  # original flock no longer proves ownership
    finally:
        runtime.close()


def test_doctor_marks_missing_capture_mount_action_not_unknown():
    from redux.core.live_health import describe_live_health
    report = describe_live_health("storage_paused", capture_mount_ok=False,
                                  free_bytes=None)
    mount = next(f for f in report["findings"]
                 if f["area"] == "capture storage")
    assert mount["status"] == "action"
    assert "REDUXCAP" in mount["summary"]
    assert "restart" in mount["remediation"]
    assert report["overall"] == "action"


def test_source_tree_is_importable_and_service_opt_in(tmp_path):
    assert callable(live.main)
    live.LiveConfig.load
