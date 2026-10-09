"""End-to-end wiring contract from radio lifecycle to prepared artifact status.

Synthetic fixtures only: this validates the complete Redux code path, not real RF.
"""
import subprocess

from redux.core import live_runtime as live
from redux.crack.ingest import CaptureIngestor, Settings, read_summary
from redux.radio import Radio


PMKID = "WPA*01*" + ("ab" * 16) + "*aabbccddeeff*112233445566*4c41422d4150***"


class FakeBettercap:
    def __init__(self):
        self.pid = 9242
        self.dead = False

    def poll(self):
        return 1 if self.dead else None

    def terminate(self):
        self.dead = True

    def wait(self, timeout=None):
        self.dead = True
        return 0

    def kill(self):
        self.dead = True


class FakeRest:
    def __init__(self, config, timeout=1.5):
        self.config = config
        self.commands = []
        self.events_pending = [
            {"tag": "wifi.ap.new", "time": 10,
             "data": {"mac": "aa:bb:cc:dd:ee:ff", "essid": "LAB-AP", "rssi": -47}}
        ]

    def session(self):
        return {"interface": "wlan1mon"}

    def run(self, command):
        self.commands.append(command)
        return {}

    def events(self, clear=False):
        result = list(self.events_pending)
        if clear:
            self.events_pending.clear()
        return result


def test_end_to_end_close_convert_and_status(tmp_path, monkeypatch):
    root = tmp_path / "captures"
    live_conf = live.LiveConfig(
        state_dir=root / "redux", active_dir=root / "active",
        capture_dir=root / "incoming", enable_web=False,
        retry_seconds=3, rotation_seconds=30,
        min_free_bytes=1024*1024,
    )
    ing_conf = Settings(
        inputs=(live_conf.capture_dir,),
        output_dir=root / "ready",
        database=root / "jobs.db",
        settle_seconds=0,
        min_free_bytes=1024*1024,
    )
    monkeypatch.setenv("REDUX_CAPTURE_DB", str(ing_conf.database))
    monkeypatch.setattr(live, "HttpTransport", FakeRest)
    clock = [100.0]
    children = []

    def spawn(argv, **kwargs):
        assert argv[0] == "bettercap"
        assert "-caplet" in argv
        child = FakeBettercap()
        children.append(child)
        return child

    def command(argv, **kwargs):
        assert argv[0] == "iw"
        return subprocess.CompletedProcess(argv, 0, "type monitor\n", "")

    def radio_inventory():
        return [Radio("wlan1mon", monitor=True,
                      bands=frozenset({"2.4", "5"}), driver="mt76x2u")]

    def convert(argv, **kwargs):
        assert argv[0] == "hcxpcapngtool"
        assert argv[-1].endswith(".pcap")
        from pathlib import Path
        Path(argv[2]).write_text(PMKID + "\n")
        return subprocess.CompletedProcess(argv, 0)

    runtime = live.LiveRuntime(live_conf, radio_probe=radio_inventory,
                               executor=command, spawn=spawn, clock=lambda: clock[0])
    try:
        assert runtime.tick() == "starting_engine"
        assert runtime.tick() == "running"
        assert runtime.tick() == "running"
        assert runtime.augur.store.count() == 1
        active = runtime.capture_file
        active.write_bytes(b"synthetic owned-AP pcap fixture")
        assert not list(live_conf.capture_dir.glob("*.pcap"))
        children[0].dead = True
        clock[0] += 1
        assert runtime.tick() == "degraded"
        finished = live_conf.capture_dir / active.name
        assert finished.read_bytes() == b"synthetic owned-AP pcap fixture"
        assert not active.exists()

        with CaptureIngestor(ing_conf, runner=convert) as worker:
            result = worker.scan()
            assert result["outcomes"].get("ready") == 1
            assert result["summary"]["hash_records"] == 1
            assert len(list(ing_conf.output_dir.glob("*.hc22000"))) == 1

        clock[0] += 3
        assert runtime.tick() == "starting_engine"
        assert runtime.tick() == "running"
        assert runtime.tick() == "running"
        status = runtime._snapshot
        assert status["runtime"]["state"] == "running"
        assert status["capture_processing"]["available"] is True
        assert status["capture_processing"]["hash_records"] == 1
        assert status["capture_processing"]["last_scan"]["scanned"] == 1
        assert status["capture_processing"]["last_scan"]["outcomes"]["ready"] == 1
        worker_finding = next(
            f for f in status["doctor"]["findings"]
            if f["area"] == "capture processing")
        assert worker_finding["status"] == "ok"
        assert "does not prove" in worker_finding["reason"]
        assert read_summary(ing_conf.database)["hash_records"] == 1
    finally:
        runtime.close()
