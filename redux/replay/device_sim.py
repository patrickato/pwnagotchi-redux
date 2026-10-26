"""Whole-device simulator — boot the REAL live stack with no Pi and no radios.

`redux simulate` stands up an actual `LiveRuntime` (the same supervisor, event
pump, Augur brain, sighting store, Doctor, status API and TFT model the device
runs) but with three things swapped for fakes:

  - the capture engine: a real `BettercapDriver` fed by a `ScheduledReplayTransport`
    that trickles a recorded scenario of Wi-Fi/BLE events in over time, instead of
    a live bettercap child + REST;
  - the radios: two plausible fake `Radio`s (onboard + an Alfa-like USB);
  - the storage: temp dirs off `/captures`, so no REDUXCAP partition is needed.

Everything else is the production code path. So `redux live status`, `redux live
doctor`, and the TFT/web dashboard all work against the simulator unchanged — which
is the point: iterate on the integrated system between bench sessions, on any
machine. The seam it uses (`LiveRuntime(driver_factory=...)`) is inert in every
production path.

Pure-ish: the scenario and transport are data + a clock; `snapshot_once` runs the
real runtime against a manual clock so tests are deterministic with no sockets.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, List, Optional

# A recorded ~20s session: APs (incl. the user's own dad24 test AP), clients,
# probes, a real handshake, and a couple of BLE devices (incl. a tracker). Event
# shape is exactly bettercap's raw format (tag/time/data); `time` is the release
# offset in seconds from capture start. Fields match what Augur reads for a
# sighting (mac/essid/channel/rssi) and a handshake (ap/station).
DEFAULT_SCENARIO: tuple = (
    {"tag": "wifi.ap.new", "time": 0.5, "data": {"mac": "18:a5:e9:65:34:d2", "essid": "dad24", "channel": 6, "rssi": -41}},
    {"tag": "wifi.ap.new", "time": 1.0, "data": {"mac": "a0:63:91:12:4c:08", "essid": "CoffeeShop", "channel": 1, "rssi": -67}},
    {"tag": "wifi.ap.new", "time": 1.4, "data": {"mac": "c8:3a:35:7b:11:90", "essid": "Linksys-2G", "channel": 11, "rssi": -72}},
    {"tag": "wifi.client.new", "time": 2.2, "data": {"mac": "3c:5a:b4:0e:aa:71", "rssi": -55}},
    {"tag": "wifi.client.probe", "time": 3.0, "data": {"mac": "3c:5a:b4:0e:aa:71", "essid": "HomeNet"}},
    {"tag": "ble.device.new", "time": 4.0, "data": {"mac": "4d:11:aa:90:0c:22", "hostname": "Pixel-7", "rssi": -60}},
    {"tag": "ble.device.new", "time": 5.0, "data": {"mac": "e2:71:3b:5f:9a:10", "hostname": "Tile", "rssi": -74}},
    {"tag": "wifi.ap.new", "time": 6.0, "data": {"mac": "f4:92:bf:aa:19:3e", "essid": "NETGEAR57", "channel": 36, "rssi": -70}},
    {"tag": "wifi.client.handshake", "time": 8.0, "data": {"ap": "18:a5:e9:65:34:d2", "station": "3c:5a:b4:0e:aa:71", "mac": "3c:5a:b4:0e:aa:71"}},
    {"tag": "wifi.ap.new", "time": 11.0, "data": {"mac": "b8:27:eb:44:55:66", "essid": "raspberrypi", "channel": 6, "rssi": -58}},
    {"tag": "ble.device.new", "time": 13.0, "data": {"mac": "7a:00:55:12:de:ad", "hostname": "JBL-Flip", "rssi": -66}},
    {"tag": "wifi.client.probe", "time": 15.0, "data": {"mac": "aa:bb:cc:11:22:33", "essid": "Starbucks WiFi"}},
    {"tag": "wifi.ap.lost", "time": 18.0, "data": {"mac": "a0:63:91:12:4c:08", "essid": "CoffeeShop"}},
)

_SIM_IFACE = "wlan1sim"


class ScheduledReplayTransport:
    """A replay transport (same interface bettercap's driver expects) that releases
    each recorded event only once its scheduled offset has elapsed on the injected
    clock, so the simulated dashboard fills in over time instead of all at once.
    Records any commands the driver issues (recon/deauth) like ReplayTransport."""

    def __init__(self, events, *, clock: Callable[[], float], loop: bool = True):
        self._clock = clock
        self._start = clock()
        self._loop = loop
        self._all = [dict(e) for e in events]
        self._pending = sorted(self._all, key=lambda e: e.get("time", 0.0))
        self.commands: list = []

    def run(self, cmd: str) -> dict:
        self.commands.append(cmd)
        return {"success": True}

    def session(self) -> dict:
        return {}

    def events(self, clear: bool = False) -> list:
        elapsed = self._clock() - self._start
        due = [e for e in self._pending if e.get("time", 0.0) <= elapsed]
        if clear:
            self._pending = [e for e in self._pending if e.get("time", 0.0) > elapsed]
            if self._loop and not self._pending:
                # replay the scenario again on the next window — a looping "day"
                self._start = self._clock()
                self._pending = sorted(self._all, key=lambda e: e.get("time", 0.0))
        return due


def sim_radios() -> List:
    """A plausible dual-radio rig: onboard brcmfmac + an Alfa-like USB adapter.
    The USB one is the preferred capture radio (matches _SIM_IFACE)."""
    from ..radio import Radio
    return [
        Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False,
              driver="brcmfmac", onboard=True),
        Radio(_SIM_IFACE, bands=frozenset({"2.4", "5"}), monitor=True, inject=True,
              driver="mt76x2u", usb_gen=2),
    ]


def make_sim_driver(scenario, *, clock: Callable[[], float]):
    """A REAL BettercapDriver whose transport is the scheduled replay — so recon /
    poll / deauth / arm-from-scope all behave exactly as on hardware."""
    from ..engine.bettercap_driver import BettercapDriver, BettercapConfig
    return BettercapDriver(config=BettercapConfig(),
                           transport=ScheduledReplayTransport(scenario, clock=clock))


def _ok_executor(*args, **kwargs):
    """Stand-in for subprocess.run: any shell-out the runtime attempts (it should
    not need one in sim) reports success and a monitor-mode iface."""
    return subprocess.CompletedProcess(args[0] if args else [], 0, "type monitor\n", "")


def _no_spawn(*args, **kwargs):
    from ..core.live_runtime import _SimProcess
    return _SimProcess()


def build_sim_config(state_root, *, web_port: int = 8080):
    from ..core.live_runtime import LiveConfig
    root = Path(state_root)
    api_port = web_port + 1 if web_port + 1 <= 65535 else web_port - 1
    return LiveConfig(
        state_dir=root / "live", capture_dir=root / "incoming", active_dir=root / "active",
        web_port=web_port, api_port=api_port,
        enable_web=True, allow_connected_capture=True,
        require_regulatory_country=False, require_usb_lease=False,
        min_free_bytes=1024 * 1024, preferred_iface=_SIM_IFACE,
    )


def _prep_dirs(state_root):
    for sub in ("live", "incoming", "active"):
        (Path(state_root) / sub).mkdir(parents=True, exist_ok=True)


def sim_runtime_factory(scenario, clock: Callable[[], float]):
    """A `run_forever`-compatible factory(config) -> LiveRuntime wired for sim."""
    def factory(config):
        from ..core.live_runtime import LiveRuntime
        return LiveRuntime(
            config,
            driver_factory=lambda iface: make_sim_driver(scenario, clock=clock),
            radio_probe=sim_radios, executor=_ok_executor, spawn=_no_spawn, clock=clock)
    return factory


def run_simulation(*, scenario=None, web_port: int = 8080, seconds: Optional[float] = None,
                   state_root=None, interval: float = 1.0):
    """Boot the simulated device and serve the real status API until `seconds`
    elapses (or forever / until interrupted). Returns nothing; this blocks."""
    scenario = DEFAULT_SCENARIO if scenario is None else scenario
    created = state_root is None
    if created:
        state_root = tempfile.mkdtemp(prefix="redux-sim-")
    _prep_dirs(state_root)
    config = build_sim_config(state_root, web_port=web_port)
    from threading import Event, Timer
    from ..core.live_runtime import run_forever
    stop = Event()
    timer = None
    if seconds:
        timer = Timer(float(seconds), stop.set)
        timer.daemon = True
        timer.start()
    try:
        run_forever(config, stop=stop, runtime_factory=sim_runtime_factory(scenario, time.monotonic),
                    interval=interval)
    finally:
        if timer is not None:
            timer.cancel()
        if created:
            shutil.rmtree(state_root, ignore_errors=True)


def snapshot_once(*, scenario=None, ticks: int = 12, step: float = 2.0, state_root=None) -> dict:
    """Run the real runtime against a manual clock for `ticks` steps and return the
    published status snapshot. Deterministic, no sockets, web disabled — for
    `redux simulate --once` and tests."""
    scenario = DEFAULT_SCENARIO if scenario is None else scenario
    created = state_root is None
    if created:
        state_root = tempfile.mkdtemp(prefix="redux-sim-")
    _prep_dirs(state_root)
    config = replace(build_sim_config(state_root), enable_web=False)
    t = [0.0]

    def clock():
        return t[0]

    runtime = sim_runtime_factory(scenario, clock)(config)
    try:
        for _ in range(max(1, ticks)):
            runtime.tick()
            t[0] += step
        return dict(getattr(runtime, "_snapshot", {}) or {})
    finally:
        try:
            runtime.close()
        except Exception:
            pass
        if created:
            shutil.rmtree(state_root, ignore_errors=True)
