"""Simulate real Redux module boundaries without pretending to validate RF hardware.

The test drives the live supervisor, produces a completed mock capture, lets the
ingestor call an injected converter, and checks durable/duplicate processing.
"""
from pathlib import Path
import subprocess

from redux.core import live_runtime as live
from redux.crack.ingest import CaptureIngestor, Settings, read_summary
from redux.radio import Radio

RECORD = "WPA*01*" + "01" * 16 + "*aabbccddeeff*112233445566*6c6162***"


class Transport:
    def __init__(self, config, timeout=1.5):
        self.config = config
        self.events_ready = [
            {"tag": "wifi.client.handshake",
             "data": {"ap": "aa:bb:cc:dd:ee:ff"}, "time": 10.0},
            {"tag": "wifi.ap.new",
             "data": {"mac": "aa:bb:cc:dd:ee:ff", "essid": "lab"}, "time": 10.0},
        ]
    def session(self):
        return {"version": "simulated"}
    def run(self, cmd):
        return {"success": True}
    def events(self, clear=False):
        result = self.events_ready
        if clear:
            self.events_ready = []
        return result


class Child:
    pid = 321
    dead = False
    def poll(self):
        return 0 if self.dead else None
    def terminate(self):
        self.dead = True
    def wait(self, timeout=None):
        return 0
    def kill(self):
        self.dead = True


def test_live_capture_to_durable_hash_and_duplicate_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "HttpTransport", Transport)
    clock = [0.0]
    active = tmp_path / "active"
    incoming = tmp_path / "incoming"
    ready = tmp_path / "ready"
    db = tmp_path / "db" / "jobs.db"
    config = live.LiveConfig(
        state_dir=tmp_path / "state",
        active_dir=active,
        capture_dir=incoming,
        enable_web=False, rotation_seconds=30,
    )
    radio = Radio("wlan1mon", monitor=True, driver="mt76x2u")
    def executor(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "type monitor\n", "")
    spawned = []
    def spawn(*args, **kwargs):
        child = Child()
        spawned.append(child)
        return child

    producer = live.LiveRuntime(config, radio_probe=lambda: [radio],
                                executor=executor, spawn=spawn,
                                clock=lambda: clock[0])
    try:
        assert producer.tick() == "starting_engine"
        open_capture = producer.capture_file
        open_capture.write_bytes(b"synthetic raw frame input, NOT valid pcap")
        assert producer.tick() == "running"
        assert producer.tick() == "running"
        assert producer.handshakes == 1
        assert producer.augur.store.count() == 1
        assert not list(incoming.glob("*.pcap"))
        clock[0] = 31
        assert producer.tick() == "rotating"
        finalized = incoming / open_capture.name
        assert finalized.is_file() and not open_capture.exists()
        assert producer.handoffs == 1

        conversions = []
        def converter(cmd, **kwargs):
            assert cmd[0] == "hcxpcapngtool"
            assert cmd[1] == "-o"
            assert cmd[-1] == str(finalized)
            Path(cmd[2]).write_text(RECORD + "\n")
            conversions.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        settings = Settings((incoming,), ready, db, settle_seconds=0)
        with CaptureIngestor(settings, runner=converter) as worker:
            first = worker.scan()
            assert first["outcomes"]["ready"] == 1
            assert worker.scan()["outcomes"]["duplicate"] == 1
            assert len(conversions) == 1
            assert worker.summary()["hash_records"] == 1
        assert len(list(ready.glob("*.hc22000"))) == 1
        assert read_summary(db)["by_status"]["ready"] == 1
    finally:
        producer.close()
