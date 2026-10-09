"""Standalone Redux passive live runtime.

A single supervisor owns its Bettercap child and its selected monitor interface.
No injection, association, deauth, attacker caplets, or password audits run here.
The existing Redux capture-ingest timer processes files independently.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import threading
import time
import tomllib
from http.server import ThreadingHTTPServer

from ..engine.bettercap_driver import BettercapConfig, BettercapDriver, HttpTransport
from ..geo import SightingStore
from ..radio import Intent, Role, decide
from ..radio.probe import probe
from ..web.status_page import make_handler, status_payload
from .augur import Augur
from .boot import atomic_checkpoint
from .signals import Signal

_LOG = logging.getLogger("redux.live")
_IFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")


@dataclass(frozen=True)
class LiveConfig:
    state_dir: Path = Path("/captures/redux")
    capture_dir: Path = Path("/captures/incoming")
    bettercap_binary: str = "bettercap"
    preferred_iface: str = ""
    api_port: int = 8081
    web_port: int = 8080
    enable_web: bool = True
    retry_seconds: int = 10
    probe_seconds: int = 30

    @classmethod
    def load(cls, path):
        with open(path, "rb") as stream:
            values = tomllib.load(stream).get("live", {})
        return cls(
            state_dir=Path(values.get("state_dir", "/captures/redux")),
            capture_dir=Path(values.get("capture_dir", "/captures/incoming")),
            bettercap_binary=values.get("bettercap_binary", "bettercap"),
            preferred_iface=values.get("preferred_iface", ""),
            api_port=int(values.get("api_port", 8081)),
            web_port=int(values.get("web_port", 8080)),
            enable_web=values.get("enable_web", True),
            retry_seconds=int(values.get("retry_seconds", 10)),
            probe_seconds=int(values.get("probe_seconds", 30)),
        )

    def validate(self):
        if not self.state_dir.is_absolute() or not self.capture_dir.is_absolute():
            raise ValueError("live directories must be absolute")
        if self.state_dir == self.capture_dir or self.state_dir in self.capture_dir.parents:
            raise ValueError("live state must not overlap capture input directory")
        if self.capture_dir in self.state_dir.parents:
            raise ValueError("capture input must not overlap state directory")
        if self.preferred_iface and not _IFACE.fullmatch(self.preferred_iface):
            raise ValueError("invalid interface name")
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", self.bettercap_binary):
            raise ValueError("bettercap_binary must be a simple executable name")
        if self.api_port == self.web_port or not all(
            1 <= port <= 65535 for port in (self.api_port, self.web_port)
        ):
            raise ValueError("API and web ports must be distinct valid ports")
        if not 2 <= self.retry_seconds <= 300 or not 5 <= self.probe_seconds <= 3600:
            raise ValueError("invalid retry/probe cadence")
        if self.enable_web is not True and self.enable_web is not False:
            raise ValueError("enable_web must be a boolean")


def select_radio(radios, preferred=""):
    """Use the existing orchestrator. Never make up absent radio capabilities."""
    radios = list(radios)
    if preferred:
        radios = [radio for radio in radios if radio.iface == preferred]
    assignment = decide(radios, Intent.RECON)
    for iface, role in assignment.roles.items():
        if role == Role.CAPTURE:
            return iface, [r for r in radios if r.iface == iface]
    return None, []


def ensure_monitor(iface, *, run=subprocess.run):
    """Configure only the selected capture interface; never change uplink radios."""
    if not _IFACE.fullmatch(iface):
        raise ValueError("invalid radio interface")
    info = run(["iw", "dev", iface, "info"], capture_output=True,
               text=True, timeout=6, check=False)
    if info.returncode:
        raise RuntimeError(f"iw cannot inspect capture interface {iface}")
    if re.search(r"^\s*type\s+monitor\s*$", info.stdout, re.M):
        return False
    commands = [
        ["ip", "link", "set", iface, "down"],
        ["iw", "dev", iface, "set", "type", "monitor"],
        ["ip", "link", "set", iface, "up"],
    ]
    for cmd in commands:
        result = run(cmd, capture_output=True, text=True, timeout=8, check=False)
        if result.returncode:
            raise RuntimeError(f"radio mode change failed: {cmd[0]} {cmd[1]}")
    checked = run(["iw", "dev", iface, "info"], capture_output=True,
                  text=True, timeout=6, check=False)
    if checked.returncode or not re.search(
        r"^\s*type\s+monitor\s*$", checked.stdout, re.M
    ):
        raise RuntimeError(f"capture radio {iface} did not enter monitor mode")
    return True


def caplet_text(iface, output, username, password, port):
    """One passive observation caplet; no transmitter-capable commands."""
    if not _IFACE.fullmatch(iface):
        raise ValueError("invalid radio interface")
    if not Path(output).is_absolute() or not re.fullmatch(r"[\w/.-]+", str(output)):
        raise ValueError("capture output path must be an absolute plain path")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", username) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", password
    ):
        raise ValueError("invalid REST credential characters")
    return (f"set wifi.interface {iface}\n"
            f"set wifi.handshakes.file {output}\n"
            "set wifi.handshakes.aggregate true\n"
            "set api.rest.address 127.0.0.1\n"
            f"set api.rest.port {port}\n"
            f"set api.rest.username {username}\n"
            f"set api.rest.password {password}\n"
            "api.rest on\n")


class LiveRuntime:
    """Owner of one Bettercap subprocess, its Augur event loop, and local UI."""

    def __init__(self, config: LiveConfig, *, radio_probe=probe, executor=subprocess.run,
                 spawn=subprocess.Popen, clock=time.monotonic):
        config.validate()
        self.config = config
        self.radio_probe = radio_probe
        self.executor = executor
        self.spawn = spawn
        self.clock = clock
        self.process = None
        self.augur = None
        self.driver = None
        self.web = None
        self.web_thread = None
        self.iface = None
        self.transport = None
        self.created = 0
        self.handshakes = 0
        self.last_error = ""
        self.next_try = 0.0
        self.last_probe = 0.0
        self.last_saved = float("-inf")
        self.saved_state = ""
        self.started = 0.0
        self.log_handle = None
        self.capture_file = None
        self._snapshot = {"runtime": {"state": "starting"}}
        self.state = "starting"
        self.config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.capture_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.config.state_dir.is_symlink() or self.config.capture_dir.is_symlink():
            raise ValueError("live directories may not be symlinks")
        os.chmod(self.config.state_dir, 0o700)
        os.chmod(self.config.capture_dir, 0o700)

    def _observe(self, emission):
        event = emission.payload.get("event")
        self.created += 1
        if getattr(event, "type", None) == "handshake":
            self.handshakes += 1

    def _checkpoint(self):
        metadata = {
            "state": self.state, "iface": self.iface or "",
            "events_seen": self.created, "handshake_events": self.handshakes,
            "pid": self.process.pid if self.process is not None else None,
            "capture_file": str(self.capture_file) if self.capture_file else "",
            "last_error": self.last_error[:180],
            "updated_utc": time.time(),
        }
        self._snapshot = {"runtime": metadata}
        if self.augur is not None:
            try:
                self._snapshot = status_payload(self.augur)
                self._snapshot["runtime"] = metadata
            except Exception as error:
                _LOG.warning("status snapshot unavailable: %s", type(error).__name__)
        if self.state != self.saved_state or self.clock() - self.last_saved >= 5:
            atomic_checkpoint(self.config.state_dir / "live.json", metadata)
            self.last_saved = self.clock()
            self.saved_state = self.state

    def _start_web(self):
        if not self.config.enable_web or self.web is not None:
            return
        server = ThreadingHTTPServer(
            ("127.0.0.1", self.config.web_port),
            make_handler(lambda: self._snapshot, token=None),
        )
        self.web = server
        self.web_thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.web_thread.start()

    def _launch(self, iface):
        ensure_monitor(iface, run=self.executor)
        credentials = secrets.token_hex(24)
        self.transport = HttpTransport(BettercapConfig(
            host="127.0.0.1", port=self.config.api_port,
            username="redux", password=credentials), timeout=1.5)
        caplet = self.config.state_dir / "bettercap-live.cap"
        # A fresh file on every engine start keeps old captures stable for ingest.
        self.capture_file = self.config.capture_dir / (
            "bettercap-" + secrets.token_hex(8) + ".pcap")
        if caplet.is_symlink():
            raise ValueError("refusing runtime caplet symlink")
        # Caplet is not JSON: stage atomically with private file permissions.
        import tempfile
        fd, name = tempfile.mkstemp(dir=self.config.state_dir, prefix=".caplet-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as out:
                out.write(caplet_text(iface, self.capture_file,
                                      "redux", credentials, self.config.api_port))
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, caplet)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        # A bounded previous log is retained across restarts; secrets stay private.
        log_path = self.config.state_dir / "bettercap.log"
        if log_path.is_symlink():
            raise ValueError("refusing engine log symlink")
        if log_path.is_file() and log_path.stat().st_size > 4 * 1024 * 1024:
            log_path.replace(self.config.state_dir / "bettercap.log.previous")
        self.log_handle = log_path.open("ab", buffering=0)
        os.chmod(self.config.state_dir / "bettercap.log", 0o600)
        self.process = self.spawn(
            [self.config.bettercap_binary, "-iface", iface, "-caplet", str(caplet),
             "-no-history", "-env-file", "", "-no-colors", "-silent"],
            stdin=subprocess.DEVNULL, stdout=self.log_handle, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.iface = iface
        self.started = self.clock()
        self.state = "starting_engine"

    def _connect(self):
        self.transport.session()  # authenticated readiness probe
        driver = BettercapDriver(config=self.transport.config, transport=self.transport)
        result = driver.set_handshake_file(str(self.capture_file))
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError("Bettercap rejected capture output configuration")
        radios = [r for r in self.radio_probe() if r.iface == self.iface]
        if not radios:
            raise RuntimeError("capture radio disappeared during startup")
        # Supervisor -> BettercapDriver is the exclusive active radio owner.
        store = SightingStore(self.config.state_dir / "sightings.db")
        try:
            agent = Augur(radios=radios, intent=Intent.RECON, driver=driver, store=store)
        except Exception:
            store.close()
            raise
        agent.bus.on(Signal.EVENT, self._observe)
        self.driver = driver
        self.augur = agent
        try:
            self._start_web()
        except OSError as error:
            _LOG.warning("local web dashboard unavailable: %s", type(error).__name__)
        self.state = "running"
        self.last_error = ""
        _LOG.info("live passive capture started on %s", self.iface)

    def _drop(self):
        if self.augur is not None:
            self.augur.store.close()
        self.augur = None
        self.driver = None
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process = None
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None
        self.iface = None
        self.capture_file = None

    def tick(self):
        now = self.clock()
        if self.process is not None and self.process.poll() is not None:
            self.last_error = "Bettercap exited unexpectedly"
            self._drop()
            self.state = "degraded"
            self.next_try = now + self.config.retry_seconds

        if self.augur is not None:
            try:
                self.augur.pump()
                if now - self.last_probe >= self.config.probe_seconds:
                    radio, _ = select_radio(self.radio_probe(), self.config.preferred_iface)
                    if radio != self.iface:
                        raise RuntimeError("capture radio removed or reassigned")
                    self.last_probe = now
            except Exception as error:
                self.last_error = f"live polling stopped: {str(error)[:150]}"
                _LOG.warning("%s", self.last_error)
                self._drop()
                self.state = "degraded"
                self.next_try = now + self.config.retry_seconds
        elif self.process is not None:
            try:
                self._connect()
            except Exception as error:
                # Startup grace permits REST to come up without restarting radio.
                if now - self.started > 15:
                    self.last_error = f"engine initialization: {str(error)[:150]}"
                    self._drop()
                    self.state = "degraded"
                    self.next_try = now + self.config.retry_seconds
        elif now >= self.next_try:
            try:
                iface, _ = select_radio(self.radio_probe(), self.config.preferred_iface)
                if not iface:
                    raise RuntimeError("no monitor-capable Wi-Fi interface detected")
                self._launch(iface)
            except Exception as error:
                self.last_error = f"engine launch: {str(error)[:150]}"
                _LOG.warning("%s", self.last_error)
                self._drop()
                self.state = "degraded"
                self.next_try = now + self.config.retry_seconds
        self._checkpoint()
        return self.state

    def close(self):
        self._drop()
        if self.web is not None:
            self.web.shutdown()
            self.web.server_close()
            self.web = None
        self.state = "stopped"
        self._checkpoint()


def run_forever(config, *, stop=None, runtime_factory=LiveRuntime, interval=1.0):
    from threading import Event
    signal_stop = stop or Event()
    if interval <= 0:
        raise ValueError("interval must be positive")
    runtime = runtime_factory(config)
    try:
        while not signal_stop.is_set():
            runtime.tick()
            signal_stop.wait(interval)
    finally:
        runtime.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Redux live passive radio supervisor")
    parser.add_argument("--config", default="/etc/redux/live.toml")
    opts = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    shutdown = threading.Event()

    def stop(signum, frame):
        shutdown.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    run_forever(LiveConfig.load(opts.config), stop=shutdown)


if __name__ == "__main__":
    main()
