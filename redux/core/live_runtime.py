"""Standalone Redux passive live runtime.

A single supervisor owns its Bettercap child and its selected monitor interface.
No injection, association, deauth, attacker caplets, or password audits run here.
The existing Redux capture-ingest timer processes files independently.
"""
from __future__ import annotations

from dataclasses import dataclass
import fcntl
import argparse
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
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
    active_dir: Path = Path("/captures/active")
    bettercap_binary: str = "bettercap"
    preferred_iface: str = ""
    api_port: int = 8081
    web_port: int = 8080
    enable_web: bool = True
    retry_seconds: int = 10
    probe_seconds: int = 30
    rotation_seconds: int = 300
    min_free_bytes: int = 64 * 1024 * 1024
    allow_connected_capture: bool = False

    @classmethod
    def load(cls, path):
        with open(path, "rb") as stream:
            values = tomllib.load(stream).get("live", {})
        return cls(
            state_dir=Path(values.get("state_dir", "/captures/redux")),
            capture_dir=Path(values.get("capture_dir", "/captures/incoming")),
            active_dir=Path(values.get("active_dir", "/captures/active")),
            bettercap_binary=values.get("bettercap_binary", "bettercap"),
            preferred_iface=values.get("preferred_iface", ""),
            api_port=int(values.get("api_port", 8081)),
            web_port=int(values.get("web_port", 8080)),
            enable_web=values.get("enable_web", True),
            retry_seconds=int(values.get("retry_seconds", 10)),
            probe_seconds=int(values.get("probe_seconds", 30)),
            rotation_seconds=int(values.get("rotation_seconds", 300)),
            min_free_bytes=int(values.get("min_free_bytes", 64 * 1024 * 1024)),
            allow_connected_capture=values.get("allow_connected_capture", False),
        )

    def validate(self):
        paths = (self.state_dir, self.capture_dir, self.active_dir)
        if not all(p.is_absolute() for p in paths):
            raise ValueError("live directories must be absolute")
        if any(a == b or a in b.parents or b in a.parents
               for i, a in enumerate(paths) for b in paths[i+1:]):
            raise ValueError("live directories must be distinct and not overlap")
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
        if not 30 <= self.rotation_seconds <= 86400:
            raise ValueError("capture rotation must be 30-86400 seconds")
        if self.min_free_bytes < 1024 * 1024:
            raise ValueError("capture minimum free bytes must be >=1 MiB")
        if self.enable_web is not True and self.enable_web is not False:
            raise ValueError("enable_web must be a boolean")
        if type(self.allow_connected_capture) is not bool:
            raise ValueError("allow_connected_capture must be a boolean")


def free_bytes(path):
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


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



def choose_safe_radio(radios, preferred="", *, run=subprocess.run,
                      allow_connected=False):
    """Prefer the best available capture radio without stealing a live uplink.

    Candidate eligibility is checked without changing an interface. If an
    adapter's current state cannot be established, skip it and try a fallback.
    Explicit operator opt-in allows the existing orchestrator's raw choice.
    """
    candidates = list(radios)
    if allow_connected:
        iface, _ = select_radio(candidates, preferred)
        return iface
    while candidates:
        iface, _ = select_radio(candidates, preferred)
        if iface is None:
            return None
        if not _IFACE.fullmatch(iface):
            candidates = [r for r in candidates if r.iface != iface]
            continue
        try:
            info = run(["iw", "dev", iface, "info"], capture_output=True,
                       text=True, timeout=6, check=False)
            if info.returncode == 0 and re.search(
                r"^\s*type\s+monitor\s*$", info.stdout, re.M
            ):
                return iface
            if info.returncode == 0 and re.search(
                r"^\s*type\s+managed\s*$", info.stdout, re.M
            ):
                link = run(["iw", "dev", iface, "link"], capture_output=True,
                           text=True, timeout=6, check=False)
                if link.returncode == 0 and not re.search(
                    r"^\s*Connected to\s+", link.stdout, re.M
                ):
                    return iface
        except (OSError, subprocess.SubprocessError):
            pass
        candidates = [r for r in candidates if r.iface != iface]
    return None


def ensure_monitor(iface, *, run=subprocess.run, allow_connected=False):
    """Configure only the selected capture interface; never change uplink radios."""
    if not _IFACE.fullmatch(iface):
        raise ValueError("invalid radio interface")
    info = run(["iw", "dev", iface, "info"], capture_output=True,
               text=True, timeout=6, check=False)
    if info.returncode:
        raise RuntimeError(f"iw cannot inspect capture interface {iface}")
    if re.search(r"^\s*type\s+monitor\s*$", info.stdout, re.M):
        return False
    if not allow_connected:
        link = run(["iw", "dev", iface, "link"], capture_output=True,
                   text=True, timeout=6, check=False)
        if link.returncode == 0 and re.search(r"^\s*Connected to\s+", link.stdout, re.M):
            raise RuntimeError(f"{iface} is a connected uplink; refusing to switch it to monitor mode")
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
            "set wifi.txpower 0\n"
            f"set wifi.handshakes.file {output}\n"
            "set wifi.handshakes.aggregate true\n"
            "set api.rest.address 127.0.0.1\n"
            f"set api.rest.port {port}\n"
            f"set api.rest.username {username}\n"
            f"set api.rest.password {password}\n"
            "api.rest on\n")


class LiveDriver(BettercapDriver):
    """Treat Bettercap REST command errors as startup/recovery failures."""

    def _checked(self, result, action):
        if not isinstance(result, dict) or result.get("error") or result.get("success") is False:
            raise RuntimeError(f"Bettercap rejected {action}")
        return result

    def set_interface(self, iface):
        return self._checked(super().set_interface(iface), "interface selection")

    def recon(self, on=True):
        return self._checked(super().recon(on), "passive recon")

    def set_handshake_file(self, path):
        return self._checked(super().set_handshake_file(path), "capture output configuration")


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
        self.last_recovery = float("-inf")
        self.saved_state = ""
        self.started = 0.0
        self.log_handle = None
        self.capture_file = None
        self.handoffs = 0
        self.last_handoff_error = ""
        self._snapshot = {"runtime": {"state": "starting"}}
        self.state = "starting"
        self.config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.capture_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.active_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if any(p.is_symlink() for p in (self.config.state_dir,
                                        self.config.capture_dir, self.config.active_dir)):
            raise ValueError("live directories may not be symlinks")
        os.chmod(self.config.state_dir, 0o700)
        os.chmod(self.config.capture_dir, 0o700)
        os.chmod(self.config.active_dir, 0o700)
        if self.config.active_dir.stat().st_dev != self.config.capture_dir.stat().st_dev:
            raise ValueError("active and incoming captures must use the same filesystem")
        lock_path = self.config.state_dir / "owner.lock"
        if lock_path.is_symlink():
            raise ValueError("refusing runtime lock symlink")
        self._lock = lock_path.open("a+b")
        os.fchmod(self._lock.fileno(), 0o600)
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock.close()
            raise RuntimeError("another Redux live runtime already owns the radio")

    def _observe(self, emission):
        event = emission.payload.get("event")
        self.created += 1
        if getattr(event, "type", None) == "handshake":
            self.handshakes += 1

    def _checkpoint(self):
        try:
            free = free_bytes(self.config.active_dir)
        except OSError:
            free = None
        metadata = {
            "state": self.state, "iface": self.iface or "",
            "events_seen": self.created, "handshake_events": self.handshakes,
            "pid": self.process.pid if self.process is not None else None,
            "capture_file": str(self.capture_file) if self.capture_file else "",
            "handed_off": self.handoffs,
            "handoff_error": self.last_handoff_error[:120],
            "free_bytes": free,
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
            try:
                atomic_checkpoint(self.config.state_dir / "live.json", metadata)
            except OSError as error:
                # Storage becoming read-only must not abandon a running child
                # or spin systemd restart loops. Keep the live snapshot usable.
                self.last_error = f"status persistence failed: {type(error).__name__}"
                _LOG.error("%s", self.last_error)
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
        ensure_monitor(iface, run=self.executor,
                       allow_connected=self.config.allow_connected_capture)
        credentials = secrets.token_hex(24)
        self.transport = HttpTransport(BettercapConfig(
            host="127.0.0.1", port=self.config.api_port,
            username="redux", password=credentials), timeout=1.5)
        caplet = self.config.state_dir / "bettercap-live.cap"
        # A fresh file on every engine start keeps old captures stable for ingest.
        self.capture_file = self.config.active_dir / (
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
        driver = LiveDriver(config=self.transport.config, transport=self.transport)
        driver.set_handshake_file(str(self.capture_file))
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
            try:
                self.augur.store.close()
            except OSError as error:
                _LOG.error("sighting store close failed: %s", type(error).__name__)
            except Exception as error:
                _LOG.error("sighting store close failed: %s", type(error).__name__)
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
        if self.capture_file is not None:
            self._handoff(self.capture_file)
        self.capture_file = None

    def _handoff(self, source):
        """Durably queue a closed capture without overwriting existing captures.

        Link first, sync the queued directory, then unlink the active name.
        An interrupted handoff may leave *both* names for one inode; the next
        recovery pass recognizes and completes that harmless intermediate state.
        """
        source = Path(source)
        try:
            st = source.lstat()
        except FileNotFoundError:
            return False
        except OSError as error:
            self.last_handoff_error = f"capture stat failed: {type(error).__name__}"
            return False
        if (not stat.S_ISREG(st.st_mode) or st.st_size == 0
                or source.parent != self.config.active_dir
                or source.suffix != ".pcap"):
            return False
        destination = self.config.capture_dir / source.name
        try:
            # Ensure capture bytes are on stable storage before publishing its name.
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                now = os.fstat(fd)
                if (now.st_ino, now.st_dev, now.st_size) != (
                    st.st_ino, st.st_dev, st.st_size
                ):
                    self.last_handoff_error = "capture changed during handoff"
                    return False
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.link(source, destination, follow_symlinks=False)
            except FileExistsError:
                # Interrupted previous handoff: these are the SAME file inode.
                prev = destination.lstat()
                if ((prev.st_dev, prev.st_ino) != (st.st_dev, st.st_ino)
                        or not stat.S_ISREG(prev.st_mode)):
                    self.last_handoff_error = "capture destination already exists"
                    return False
            self._sync_directory(self.config.capture_dir)
            source.unlink()
            self._sync_directory(self.config.active_dir)
        except OSError as error:
            self.last_handoff_error = f"handoff failed: {type(error).__name__}"
            return False
        self.handoffs += 1
        self.last_handoff_error = ""
        return True

    @staticmethod
    def _sync_directory(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _recover_abandoned(self):
        """Resume stale captures after abrupt power loss; never touch fresh files."""
        for path in self.config.active_dir.iterdir():
            if (path.suffix != ".pcap" or path == self.capture_file
                    or path.is_symlink() or not path.is_file()):
                continue
            try:
                if time.time() - path.stat().st_mtime >= 60:
                    self._handoff(path)
            except OSError:
                # A concurrent rename/removal or media I/O error is retryable.
                continue

    def tick(self):
        now = self.clock()
        if now - self.last_recovery >= 20:
            self._recover_abandoned()
            self.last_recovery = now
        if self.process is not None and self.process.poll() is not None:
            self.last_error = "Bettercap exited unexpectedly"
            self._drop()
            self.state = "degraded"
            self.next_try = now + self.config.retry_seconds

        if self.augur is not None:
            try:
                if free_bytes(self.config.active_dir) < self.config.min_free_bytes:
                    self._drop()
                    self.state = "storage_paused"
                    self.last_error = "capture partition is below free-space reserve"
                    self.next_try = now + self.config.retry_seconds
                    self._checkpoint()
                    return self.state
                if now - self.started >= self.config.rotation_seconds:
                    self._drop()
                    self.state = "rotating"
                    self.next_try = now + 1
                    self._checkpoint()
                    return self.state
                self.augur.pump()
                if now - self.last_probe >= self.config.probe_seconds:
                    radio = choose_safe_radio(self.radio_probe(), self.config.preferred_iface,
                                              run=self.executor,
                                              allow_connected=self.config.allow_connected_capture)
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
                if free_bytes(self.config.active_dir) < self.config.min_free_bytes:
                    raise RuntimeError("capture partition is below free-space reserve")
                iface = choose_safe_radio(self.radio_probe(), self.config.preferred_iface,
                                               run=self.executor,
                                               allow_connected=self.config.allow_connected_capture)
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
        fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
        self._lock.close()


def preflight(config, *, which=shutil.which, radio_probe=probe,
              radio_run=subprocess.run, is_mount=os.path.ismount,
              available_bytes=free_bytes):
    """Read-only hardware/OS readiness check. Never changes a network interface."""
    errors, warnings = [], []
    try:
        config.validate()
    except (ValueError, TypeError) as exc:
        return {"ready": False, "errors": [str(exc)], "warnings": [],
                "capture_radio": None, "checks": {}}
    checks = {}
    checks["bettercap"] = bool(which(config.bettercap_binary))
    checks["iw"] = bool(which("iw"))
    checks["ip"] = bool(which("ip"))
    checks["converter"] = bool(which("hcxpcapngtool"))
    for key in ("bettercap", "iw", "ip", "converter"):
        if not checks[key]:
            errors.append(f"{key} executable unavailable")
    capture_mount = Path("/captures")
    # On the baked standalone image /captures is a separate ext4 filesystem;
    # on a source checkout this is useful diagnostic information only.
    if str(config.active_dir).startswith("/captures/"):
        checks["capture_mount"] = bool(is_mount(capture_mount))
        if not checks["capture_mount"]:
            errors.append("writable REDUXCAP partition is not mounted at /captures")
    else:
        checks["capture_mount"] = None
    checks["storage_paths"] = all(
        p.is_dir() and not p.is_symlink()
        for p in (config.state_dir, config.active_dir, config.capture_dir)
    )
    if not checks["storage_paths"]:
        warnings.append("capture/state directories not all provisioned")
    try:
        free = available_bytes(config.active_dir if config.active_dir.exists()
                               else config.active_dir.parent)
        checks["free_bytes"] = free
        if free < config.min_free_bytes:
            errors.append("capture storage below configured free-space reserve")
    except OSError as error:
        checks["free_bytes"] = None
        errors.append(f"capture free-space probe failed: {type(error).__name__}")
    try:
        ifaces = list(radio_probe())
        iface = choose_safe_radio(ifaces, config.preferred_iface,
                                  run=radio_run,
                                  allow_connected=config.allow_connected_capture)
        checks["capture_interface_found"] = bool(iface)
        if not iface:
            errors.append("no safe monitor-capable capture radio detected (connected or unavailable)")
    except (OSError, RuntimeError, ValueError) as error:
        iface = None
        checks["capture_interface_found"] = False
        errors.append(f"radio probe failed: {type(error).__name__}")
    return {"ready": not errors, "errors": errors, "warnings": warnings,
            "capture_radio": iface, "checks": checks}


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
    parser.add_argument("--check", action="store_true",
                        help="read-only hardware and image preflight (no RF changes)")
    opts = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = LiveConfig.load(opts.config)
    if opts.check:
        outcome = preflight(config)
        print(json.dumps(outcome, indent=2, sort_keys=True))
        return 0 if outcome["ready"] else 2
    shutdown = threading.Event()

    def stop(signum, frame):
        shutdown.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    run_forever(config, stop=shutdown)


if __name__ == "__main__":
    main()
