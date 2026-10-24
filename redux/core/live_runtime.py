"""Standalone Redux live runtime.

A single supervisor owns its Bettercap child and its selected monitor interface.
It is PASSIVE by default — no injection, association, deauth, attacker caplets, or
password audits. The one exception is lab mode (your own gear): when the persisted
scope has lab_mode on, the pump actively deauths seen APs to force 4-ways (Augur's
auto-offense), every frame logged with a glass-box reason. Lab mode is off in the
shipped image, so the default stays fully passive. The existing Redux capture-ingest
and audit timers process captured files independently.
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
import sqlite3
import stat
import subprocess
import threading
import time
import tomllib
from http.server import ThreadingHTTPServer

from ..crack.ingest import read_summary
from ..storage_mount import capture_mount_snapshot
from ..engine.bettercap_driver import BettercapConfig, BettercapDriver, HttpTransport
from ..geo import SightingStore
from ..radio import Intent, Role, decide
from ..radio.probe import probe
from ..radio.live_lease import UsbLeaseGuard, FileLeaseEvidence, KernelUsbVerifier, LeaseLost, usb_attached
from ..radio.split_integration import CaptureLease
from ..radio.link_type import read_link_type, link_assessment
from ..web.status_page import make_handler, status_payload
from .augur import Augur
from .telemetry import ResourceMonitor
from .doctor import Doctor, DoctorInputs
from .boot import atomic_checkpoint
from .live_health import describe_live_health
from .runtime_identity import configuration_checkpoint
from .regulatory import read_country, verify_country
from .signals import Signal

_LOG = logging.getLogger("redux.live")
_IFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")


@dataclass(frozen=True)
class LiveConfig:
    state_dir: Path = Path("/captures/live")
    capture_dir: Path = Path("/captures/incoming")
    active_dir: Path = Path("/captures/active")
    bettercap_binary: str = "bettercap"
    preferred_iface: str = ""
    api_port: int = 8081
    web_port: int = 8080
    enable_web: bool = True
    # Where the live dashboard listens. Default localhost (loopback only). A wider
    # scope (lan/tailscale) lets a phone/laptop reach the REAL daemon dashboard,
    # but is fail-closed: it is honoured only with a web_token set, else loopback.
    web_bind_scope: str = "localhost"
    web_token: str = ""
    enable_native_plugins: bool = False
    retry_seconds: int = 10
    probe_seconds: int = 30
    rotation_seconds: int = 300
    min_free_bytes: int = 64 * 1024 * 1024
    max_log_bytes: int = 4 * 1024 * 1024
    pipeline_database: Path | None = None
    pipeline_stale_seconds: int = 180
    status_sample_seconds: int = 5
    allow_connected_capture: bool = False
    require_usb_lease: bool = False
    usb_evidence_file: Path | None = None
    usb_management_peer: str = ""
    require_regulatory_country: bool = False
    regulatory_country_file: Path = Path("/etc/redux/regulatory-country")

    @classmethod
    def load(cls, path):
        with open(path, "rb") as stream:
            values = tomllib.load(stream).get("live", {})
        return cls(
            state_dir=Path(values.get("state_dir", "/captures/live")),
            capture_dir=Path(values.get("capture_dir", "/captures/incoming")),
            active_dir=Path(values.get("active_dir", "/captures/active")),
            bettercap_binary=values.get("bettercap_binary", "bettercap"),
            preferred_iface=values.get("preferred_iface", ""),
            api_port=int(values.get("api_port", 8081)),
            web_port=int(values.get("web_port", 8080)),
            enable_web=values.get("enable_web", True),
            web_bind_scope=str(values.get("web_bind_scope", "localhost")),
            web_token=str(values.get("web_token", "")),
            enable_native_plugins=values.get("enable_native_plugins", False),
            retry_seconds=int(values.get("retry_seconds", 10)),
            probe_seconds=int(values.get("probe_seconds", 30)),
            rotation_seconds=int(values.get("rotation_seconds", 300)),
            min_free_bytes=int(values.get("min_free_bytes", 64 * 1024 * 1024)),
            max_log_bytes=int(values.get("max_log_bytes", 4 * 1024 * 1024)),
            pipeline_database=(Path(values["pipeline_database"])
                               if values.get("pipeline_database") else None),
            pipeline_stale_seconds=int(values.get("pipeline_stale_seconds", 180)),
            status_sample_seconds=int(values.get("status_sample_seconds", 5)),
            allow_connected_capture=values.get("allow_connected_capture", False),
            require_usb_lease=values.get("require_usb_lease", False),
            usb_evidence_file=Path(values["usb_evidence_file"]) if values.get("usb_evidence_file") else None,
            usb_management_peer=values.get("usb_management_peer", ""),
            require_regulatory_country=values.get("require_regulatory_country", False),
            regulatory_country_file=Path(values.get("regulatory_country_file",
                                                    "/etc/redux/regulatory-country")),
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
        if not 65536 <= self.max_log_bytes <= 64 * 1024 * 1024:
            raise ValueError("max_log_bytes must be between 64 KiB and 64 MiB")
        if self.pipeline_database is not None and not self.pipeline_database.is_absolute():
            raise ValueError("pipeline_database must be absolute")
        if not 60 <= self.pipeline_stale_seconds <= 3600:
            raise ValueError("pipeline_stale_seconds must be 60-3600 seconds")
        if not 1 <= self.status_sample_seconds <= 30:
            raise ValueError("status_sample_seconds must be 1-30 seconds")
        if type(self.enable_native_plugins) is not bool:
            raise ValueError("enable_native_plugins must be a boolean")
        if self.enable_web is not True and self.enable_web is not False:
            raise ValueError("enable_web must be a boolean")
        if type(self.allow_connected_capture) is not bool:
            raise ValueError("allow_connected_capture must be a boolean")
        if type(self.require_usb_lease) is not bool:
            raise ValueError("require_usb_lease must be a boolean")
        if self.require_usb_lease:
            import ipaddress
            ipaddress.ip_address(self.usb_management_peer)
            if not self.preferred_iface or self.allow_connected_capture:
                raise ValueError("USB lease needs a pinned interface without connected-capture override")
            if self.usb_evidence_file is None or not self.usb_evidence_file.is_absolute():
                raise ValueError("USB lease requires an absolute evidence path")
        if type(self.require_regulatory_country) is not bool:
            raise ValueError("require_regulatory_country must be a boolean")
        if self.regulatory_country_file != Path("/etc/redux/regulatory-country"):
            raise ValueError("regulatory_country_file must use the baked system path")


def resolve_web_bind(bind_scope, token):
    """(host, token, error) for the daemon dashboard listener.

    FAIL-CLOSED: the dashboard is never exposed off-box without a token. An
    off-box scope (lan/tailscale) with no token falls back to loopback and returns
    a human-readable reason; localhost always binds loopback. This mirrors the
    config-layer rule (config.py: a token is required for an off-box bind)."""
    from ..web.status_page import _SCOPE_HOST
    host = _SCOPE_HOST.get(str(bind_scope), "127.0.0.1")
    tok = str(token) or None
    if host != "127.0.0.1" and not tok:
        return "127.0.0.1", None, (
            f"web.bind_scope={bind_scope} ignored: a web_token is required for an "
            f"off-box bind — serving on loopback only")
    return host, tok, ""


def free_bytes(path):
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def _gps_from_env():
    """A position_provider from a serial GPS named by $REDUX_GPS (e.g.
    /dev/ttyUSB0), or None when unset — so geo features light up when a GPS puck
    is present, with no behavior change when it isn't. Best-effort, never raises."""
    dev = os.environ.get("REDUX_GPS")
    if not dev:
        return None
    try:
        from ..geo.gps import serial_position_provider
        return serial_position_provider(dev)
    except Exception:
        return None


def _arm_sentinel_from_env(agent):
    """Arm the deploy-and-watch Sentinel when $REDUX_SENTINEL is set (e.g.
    'armed', or a min severity like 'warning'/'critical'), routing detector/CSI
    alerts to the live log. Unset -> off, so no behavior change by default.
    Best-effort: never breaks startup."""
    spec = os.environ.get("REDUX_SENTINEL")
    if not spec:
        return None
    try:
        from ..sentinel import CallableNotifier
        low = spec.strip().lower()
        armed = low == "armed"
        if low == "warning":          # accept the value the docstring advertises;
            low = "warn"              # the Severity enum spells it "warn"
        sev = low if low in ("info", "warn", "critical") else "warn"
        notifier = CallableNotifier(lambda ev: _LOG.warning(
            "sentinel: %s", getattr(ev, "summary", getattr(ev, "reason", ev))))
        return agent.enable_sentinel(notifier=notifier, armed=armed, min_severity=sev)
    except Exception:
        return None


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
                 spawn=subprocess.Popen, clock=time.monotonic, lease_guard=None,
                 radio_inventory_provider=None, resource_monitor=None):
        config.validate()
        self.config = config
        self.capture_mount_required = any(
            Path("/captures") == path or Path("/captures") in path.parents
            for path in (config.state_dir, config.capture_dir, config.active_dir)
        )
        self.capture_mount_lost = False
        # Bind lifetime of the radio owner lock to this exact ext4 mount.
        # A replacement mount has a new kernel mount ID even on the same card.
        self.capture_mount_identity = (
            capture_mount_snapshot() if self.capture_mount_required else None
        )
        # Refuse to create /captures/... on the root filesystem when REDUXCAP
        # is missing. This precedes *all* mkdir, chmod and lock operations.
        if not self._capture_mount_ok():
            raise RuntimeError("REDUXCAP ext4 partition is not mounted at /captures")
        self.radio_probe = radio_probe
        self.radio_inventory_provider = radio_inventory_provider
        self._radio_inventory = None
        self._radio_inventory_error = ""
        self._radio_inventory_sampled_at = float("-inf")
        self._radio_inventory_lock = threading.Lock()
        self._radio_inventory_inflight = False
        self._radio_inventory_thread = None
        self._closing = False
        self.native_plugins = None
        self.native_next_try = float("-inf")
        self.executor = executor
        self.spawn = spawn
        self.clock = clock
        self.resources = resource_monitor if resource_monitor is not None else ResourceMonitor()
        self.lease_guard = None
        if config.require_usb_lease:
            self.lease_guard = lease_guard or UsbLeaseGuard(
                FileLeaseEvidence(config.usb_evidence_file),
                KernelUsbVerifier(config.usb_management_peer, run=executor), clock=clock)
        self._lease_interlock = False
        self.process = None
        self.augur = None
        self.driver = None
        self.web = None
        self.web_thread = None
        self.web_next_try = float("-inf")
        self.web_error = ""
        self.iface = None
        self.transport = None
        self.created = 0
        self.handshakes = 0
        self.last_error = ""
        self.next_try = 0.0
        self.failure_streak = 0
        self.retry_delay = 0.0
        self.engine_attempts = 0
        self.last_probe = 0.0
        self.last_saved = float("-inf")
        self.last_recovery = float("-inf")
        self.saved_state = ""
        self.started = 0.0
        self.log_handle = None
        self.log_truncations = 0
        self.log_error = ""
        self.capture_file = None
        self.handoffs = 0
        self.last_handoff_error = ""
        self.telemetry_next_try = float("-inf")
        self.sighting_loss_events = 0
        self.last_sighting_loss_reason = ""
        self.observer_errors = {}
        self.persistence_error = ""
        self._visual_next_try = float("-inf")
        self._snapshot = {"runtime": {"state": "starting", "updated_utc": time.time()},
                          "doctor": Doctor().report(DoctorInputs()),
                          "telemetry": None, "governor": self.resources.governor_status(),
                          "capture_processing": {"available": False, "reason": "processing not yet observed"}}
        self._cached_visual = None
        self._visual_dirty = True
        self._visual_sampled_at = float("-inf")
        self._visual_sampled_utc = None
        self._visual_revision = 0
        self._cached_processing = None
        self._processing_sampled_at = float("-inf")
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
        self._load_lease_interlock()

    def _load_lease_interlock(self):
        if self.lease_guard is None:
            return
        path = self.config.state_dir / 'usb-lease-interlock.json'
        try:
            if path.is_symlink() or path.stat().st_size > 4096:
                raise ValueError
            d = json.loads(path.read_text())
            if not isinstance(d, dict):
                raise ValueError
            if d.get('blocked') is not False:
                raise ValueError
            identity = d.get('identity')
            if identity:
                if (not isinstance(identity, dict) or set(identity) != {'iface','phy','ifindex'}
                        or type(identity['ifindex']) is not int or identity['ifindex'] <= 0
                        or not isinstance(identity['iface'], str) or not _IFACE.fullmatch(identity['iface'])
                        or not isinstance(identity['phy'], str) or not re.fullmatch(r'phy[0-9]+', identity['phy'])):
                    raise ValueError
                self.lease_guard.expected = CaptureLease(**identity)
            kernel_identity = d.get('kernel_identity')
            if kernel_identity is not None and (not isinstance(kernel_identity, list)
                    or len(kernel_identity) != 2 or not all(type(x) is int and x > 0 for x in kernel_identity)):
                raise ValueError
            if kernel_identity is not None and hasattr(self.lease_guard.verify_kernel, 'net_identity'):
                self.lease_guard.verify_kernel.net_identity = tuple(kernel_identity)
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError):
            self._lease_interlock = self.lease_guard.blocked = True

    def _save_lease_interlock(self, blocked):
        if self.lease_guard is None:
            return
        from dataclasses import asdict
        self._lease_interlock = blocked
        self.lease_guard.blocked = blocked
        try:
            atomic_checkpoint(self.config.state_dir / 'usb-lease-interlock.json', {
                'blocked': blocked,
                'identity': asdict(self.lease_guard.expected) if self.lease_guard.expected else None,
                'kernel_identity': getattr(self.lease_guard.verify_kernel, 'net_identity', None),
            })
        except Exception:
            self._lease_interlock = self.lease_guard.blocked = True
            raise LeaseLost('USB interlock persistence unavailable') from None

    def _check_usb_lease(self):
        # With the field lease disabled, a USB adapter is a first-class capture
        # radio (guarded only by ensure_monitor's connected-uplink refusal), so
        # there is nothing to enforce here. The lease checks apply only when the
        # operator opted into the full provenance model (lease_guard present).
        if self.lease_guard is not None:
            try:
                self.lease_guard.check(owner_pid=getattr(self.process, 'pid', None))
            except BaseException:
                try:
                    self._save_lease_interlock(True)
                except LeaseLost:
                    pass
                raise

    def rearm_usb_capture(self, *, owner_authorized=False):
        """Explicit local operator integration hook; never an automatic retry."""
        if self.process is not None or self.augur is not None or owner_authorized is not True:
            raise LeaseLost('re-arm requires explicit approval and a reaped writer')
        if self.lease_guard is None:
            raise LeaseLost('USB lease mode disabled')
        self.lease_guard.rearm(owner_authorized=True)
        self._save_lease_interlock(False)
        self.state = 'degraded'
        self.next_try = self.clock()

    def _capture_mount_ok(self):
        return (not self.capture_mount_required
                or (self.capture_mount_identity is not None
                    and capture_mount_snapshot() == self.capture_mount_identity))

    def _refresh_radio_inventory(self, now):
        """Schedule bounded read-only radio sampling off the capture supervisor loop."""
        provider = self.radio_inventory_provider
        if provider is None or self._closing:
            return
        with self._radio_inventory_lock:
            if self._radio_inventory_inflight:
                return
            if (now >= self._radio_inventory_sampled_at
                    and now - self._radio_inventory_sampled_at < 10):
                return
            self._radio_inventory_sampled_at = now
            self._radio_inventory_inflight = True

        def sample_once():
            try:
                from ..radio.display_inventory import normalize_sample
                sample = normalize_sample(provider())
                with self._radio_inventory_lock:
                    if not self._closing:
                        self._radio_inventory = sample
                        self._radio_inventory_error = ""
            except Exception as error:
                with self._radio_inventory_lock:
                    if not self._closing:
                        self._radio_inventory_error = type(error).__name__
                # Keep the last timestamped sample. The UI expires it after
                # 20s; a failed refresh cannot make old hardware look live.
                _LOG.debug("read-only radio inventory unavailable: %s",
                           type(error).__name__)
            finally:
                with self._radio_inventory_lock:
                    self._radio_inventory_inflight = False

        worker = threading.Thread(target=sample_once, daemon=True,
                                  name="redux-radio-inventory")
        with self._radio_inventory_lock:
            self._radio_inventory_thread = worker
        try:
            worker.start()
        except RuntimeError as error:
            with self._radio_inventory_lock:
                self._radio_inventory_inflight = False
                self._radio_inventory_thread = None
                self._radio_inventory_error = type(error).__name__

    def _observe(self, emission):
        event = emission.payload.get("event")
        # Accumulate events without triggering an expensive full-table visual
        # query on every tick during busy radio activity. The next bounded
        # visual sample will include all committed events seen so far.
        self._visual_dirty = True
        self.created += 1
        if getattr(event, "type", None) == "handshake":
            self.handshakes += 1

    def _status_observation(self, name, callback, fallback):
        """Isolate diagnostic producers; never execute capture control here."""
        try:
            value = callback()
            if not isinstance(value, dict):
                raise ValueError("invalid observer result")
            if len(json.dumps(value, allow_nan=False)) > 65536:
                raise ValueError("oversized observer result")
            self.observer_errors.pop(name, None)
            return value
        except Exception as error:
            self.observer_errors[name] = type(error).__name__
            return fallback

    def _checkpoint(self):
        if getattr(self.resources, "collection_error", ""):
            self.observer_errors["resources"] = self.resources.collection_error
        else:
            self.observer_errors.pop("resources", None)
        mount_ok = self._capture_mount_ok() and not self.capture_mount_lost
        if mount_ok:
            try:
                free = free_bytes(self.config.active_dir)
            except OSError:
                free = None
        else:
            free = None
        pending = (len(self.augur._sighting_buffer)
                   if self.augur is not None else None)
        write_error = (self.augur._sighting_flush_error
                       if self.augur is not None else "")
        failures = (self.augur._sighting_flush_failures
                    if self.augur is not None else 0)
        now = self.clock()
        self._refresh_radio_inventory(now)
        with self._radio_inventory_lock:
            inventory_error = self._radio_inventory_error
        if inventory_error:
            self.observer_errors["radio_inventory"] = inventory_error
        else:
            self.observer_errors.pop("radio_inventory", None)
        # Read only the active capture iface's kernel-reported ARPHRD.
        # A monitor-mode label does not imply native 802.11 framing.
        capture_link_type = read_link_type(self.iface)
        metadata = {
            "state": self.state, "iface": self.iface or "",
            "observer_errors": dict(self.observer_errors),
            "persistence_error": self.persistence_error,
            "capture_link_type": capture_link_type,
            "capture_link_assessment": link_assessment(capture_link_type),
            "events_seen": self.created, "handshake_events": self.handshakes,
            "pid": self.process.pid if self.process is not None else None,
            "capture_file": str(self.capture_file) if self.capture_file else "",
            "handed_off": self.handoffs,
            "handoff_error": self.last_handoff_error[:120],
            "sightings_pending": pending,
            "sightings_write_error": write_error[:160],
            "sightings_write_failures": failures,
            "sightings_lost_on_restart": self.sighting_loss_events,
            "sightings_loss_reason": self.last_sighting_loss_reason[:140],
            "dashboard_active": self.web is not None if self.config.enable_web else None,
            "dashboard_error": self.web_error[:120],
            "log_bytes": self._log_size(),
            "log_truncations": self.log_truncations,
            "log_error": self.log_error[:120],
            "free_bytes": free,
            "capture_mount": (mount_ok if self.capture_mount_required else None),
            "last_error": self.last_error[:180],
            "engine_attempts": self.engine_attempts,
            "failure_streak": self.failure_streak,
            "retry_delay_seconds": self.retry_delay,
            "retry_in_seconds": (round(max(0.0, self.next_try - now), 1)
                                 if self.state in ("degraded", "storage_paused")
                                 else 0.0),
            "updated_utc": time.time(),
        }
        pipeline_path = (self.config.pipeline_database
                         or self.config.capture_dir.parent / "jobs.db")
        if not mount_ok:
            processing = {"available": False, "reason": "REDUXCAP mount unavailable"}
            self._cached_processing = None
        elif (self._cached_processing is None
              or now < self._processing_sampled_at
              or now - self._processing_sampled_at >= self.config.status_sample_seconds):
            processing = self._status_observation("processing", lambda: read_summary(pipeline_path),
                {"available": False, "reason": "processing observer unavailable"})
            self._cached_processing = processing
            self._processing_sampled_at = now
        else:
            processing = self._cached_processing
        # Assemble a complete local copy, then publish it in one assignment.
        # The read-only HTTP thread must never see a partially written report.
        snapshot = {"runtime": metadata, "capture_processing": processing,
                    "telemetry": self.resources.status(),
                    "governor": self.resources.governor_status()}
        report = Doctor().report(DoctorInputs(governor=self.resources.decision,
                                              telemetry=self.resources.status()))
        if self.augur is not None:
            elapsed = now - self._visual_sampled_at
            event_min = min(2, self.config.status_sample_seconds)
            if now >= self._visual_next_try and (self._cached_visual is None or elapsed < 0
                    or elapsed >= self.config.status_sample_seconds
                    or (self._visual_dirty and elapsed >= event_min)):
                try:
                    visual = status_payload(self.augur, processing_report=processing)
                    self._cached_visual = visual
                    self._visual_sampled_at = now
                    self._visual_sampled_utc = time.time()
                    self._visual_revision += 1
                    self._visual_dirty = False
                    self._visual_next_try = float("-inf")
                    self.observer_errors.pop("visual", None)
                except Exception as error:
                    self._cached_visual = None
                    self._visual_sampled_utc = None
                    self._visual_next_try = now + self.config.status_sample_seconds
                    self.observer_errors["visual"] = type(error).__name__
                    _LOG.warning("status snapshot unavailable: %s", type(error).__name__)
            if self._cached_visual is not None:
                snapshot = dict(self._cached_visual)
                snapshot["runtime"] = metadata
                snapshot["capture_processing"] = processing
            # The runtime and Augur share one ResourceMonitor. Read it directly
            # after the visual cache so stale/broken presentation adapters cannot
            # overwrite current sensor/Governor evidence.
            snapshot["governor"] = self.resources.governor_status()
            snapshot["telemetry"] = self.resources.status()
            try:
                report = self._status_observation("doctor", self.augur.doctor_report, report)
                if not isinstance(report.get("findings"), list):
                    raise ValueError("invalid Doctor findings")
            except Exception as error:
                report = Doctor().report(DoctorInputs())
                self.observer_errors["doctor"] = type(error).__name__
                _LOG.warning("Doctor snapshot unavailable: %s", type(error).__name__)
        # Read a previously published 8 KiB integrity receipt only; never
        # hash artifacts, open the ledger, or run a scan in the live loop.
        from ..crack.integrity_receipt import read_cached_receipt
        artifact_integrity = (
            read_cached_receipt(self.config.capture_dir.parent /
                                "diagnostics" / "artifact-integrity.json")
            if mount_ok else
            {"status": "unknown", "reason": "receipt_unavailable",
             "checked": 0, "ready_rows": 0, "age_seconds": None}
        )
        snapshot["artifact_integrity"] = artifact_integrity
        metadata["visual_sampled_utc"] = self._visual_sampled_utc if self.augur else None
        metadata["visual_max_age_seconds"] = max(12, 2 * self.config.status_sample_seconds)
        metadata["visual_revision"] = self._visual_revision if self.augur else None
        if self.native_plugins is not None:
            try:
                snapshot["plugins"] = self.native_plugins.summary()
            except Exception:
                from ..packs.status import unavailable
                snapshot["plugins"] = unavailable()
        elif self.config.enable_native_plugins:
            from ..packs.status import unavailable
            snapshot["plugins"] = unavailable()
        if self.config.enable_native_plugins:
            from .doctor import with_native_plugins
            report = with_native_plugins(report, snapshot.get("plugins", {}))
        def health_view():
            return describe_live_health(
                self.state, iface=self.iface or "",
                capture_link_type=capture_link_type, free_bytes=free,
                reserve_bytes=self.config.min_free_bytes, handoffs=self.handoffs,
                last_error=self.last_error, handoff_error=self.last_handoff_error,
                doctor_report=report,
                dashboard_enabled=self.config.enable_web,
                dashboard_active=self.web is not None,
                dashboard_error=self.web_error,
                sighting_pending=pending, sighting_write_error=write_error,
                sighting_lost=self.sighting_loss_events,
                engine_log_error=self.log_error,
                capture_mount_ok=(mount_ok if self.capture_mount_required else None),
                processing_report=processing,
                processing_stale_seconds=self.config.pipeline_stale_seconds,
                observer_errors=dict(self.observer_errors),
                persistence_error=self.persistence_error,
                artifact_integrity=artifact_integrity,
                failure_streak=self.failure_streak,
                retry_in_seconds=metadata["retry_in_seconds"],
            )
        health = health_view()
        metadata["health"] = health["overall"]
        metadata["health_unknown_areas"] = len(health["coverage"]["not_assessed"])
        snapshot["doctor"] = health
        with self._radio_inventory_lock:
            radio_inventory = self._radio_inventory
        if radio_inventory is not None:
            snapshot["radio_inventory"] = radio_inventory
        metadata["observer_errors"] = dict(self.observer_errors)
        from ..display.ui_state import model as ui_model
        snapshot["ui"] = ui_model(snapshot)
        previous_persistence_error = self.persistence_error
        if mount_ok and (self.state != self.saved_state or self.clock() - self.last_saved >= 5):
            try:
                private_metadata = dict(metadata)
                private_metadata["persistence_error"] = ""
                private_metadata["configuration_identity"] = configuration_checkpoint(self.config)
                private_metadata["observability"] = {
                    "schema": 1, "sensor_measured_at": snapshot.get("telemetry", {}).get("measured_at") if isinstance(snapshot.get("telemetry"), dict) else None,
                    "sensor_freshness": snapshot["ui"]["sensors"]["state"],
                    "visual_freshness": snapshot["ui"]["visual"]["state"],
                    "processing_available": processing.get("available") is True,
                }
                atomic_checkpoint(self.config.state_dir / "live.json", private_metadata)
                self.persistence_error = ""
            except (OSError, ValueError) as error:
                # Storage becoming read-only must not abandon a running child
                # or spin systemd restart loops. Keep the live snapshot usable.
                self.persistence_error = f"status persistence failed: {type(error).__name__}"
                _LOG.error("%s", self.persistence_error)
            self.last_saved = self.clock()
            self.saved_state = self.state
        if previous_persistence_error != self.persistence_error:
            metadata["persistence_error"] = self.persistence_error
            snapshot["doctor"] = health_view()
            metadata["health"] = snapshot["doctor"]["overall"]
            metadata["health_unknown_areas"] = len(snapshot["doctor"]["coverage"]["not_assessed"])
            snapshot["ui"] = ui_model(snapshot)
        self._snapshot = snapshot

    def _maintain_web(self, now):
        """Keep the loopback read-only diagnostics server up, independent of the
        capture mount. It serves the already-published in-memory snapshot (which
        carries the Doctor report), so it stays available to explain a storage
        outage instead of going dark with it."""
        if self.web is not None and self.web_thread is not None:
            if not self.web_thread.is_alive():
                self.web_error = "local dashboard thread stopped unexpectedly"
                self.web.server_close()
                self.web = None
                self.web_thread = None
                self.web_next_try = now + 30
                _LOG.warning("%s", self.web_error)
        if self.config.enable_web and self.web is None and now >= self.web_next_try:
            try:
                self._start_web()
            except (OSError, RuntimeError) as error:
                self.web_error = f"local dashboard startup failed: {type(error).__name__}"
                self.web_next_try = now + 30
                _LOG.warning("%s", self.web_error)

    def _start_web(self):
        if not self.config.enable_web or self.web is not None:
            return
        host, token, bind_error = resolve_web_bind(
            getattr(self.config, "web_bind_scope", "localhost"),
            getattr(self.config, "web_token", ""))
        server = ThreadingHTTPServer(
            (host, self.config.web_port),
            make_handler(lambda: self._snapshot, token=token,
                         plugin_runtime=lambda: self.native_plugins.snapshot() if self.native_plugins else {}),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            thread.start()
        except Exception:
            server.server_close()
            raise
        # Only mark the listener active *after* its server thread launched.
        self.web = server
        self.web_thread = thread
        # Carry the fail-closed note (if an off-box bind was refused for lack of a
        # token) so the operator can see why the dashboard stayed on loopback.
        self.web_error = bind_error

    def _log_size(self):
        if self.log_handle is None:
            return None
        try:
            return os.fstat(self.log_handle.fileno()).st_size
        except OSError:
            return None

    def _open_engine_log(self):
        path = self.config.state_dir / "bettercap.log"
        old = self.config.state_dir / "bettercap.log.previous"
        # Older builds may have left a huge, abandoned archive. Do not allow a
        # pre-existing symlink or hardlink to point cleanup outside state_dir.
        if old.is_symlink():
            raise ValueError("refusing engine log archive symlink")
        if old.exists():
            st = old.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise ValueError("refusing non-regular engine log archive")
            if st.st_size > self.config.max_log_bytes:
                fd = os.open(old, os.O_WRONLY | os.O_NOFOLLOW)
                try:
                    seen = os.fstat(fd)
                    if not stat.S_ISREG(seen.st_mode) or seen.st_nlink != 1:
                        raise ValueError("refusing linked engine log archive")
                    os.ftruncate(fd, 0)
                finally:
                    os.close(fd)
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise ValueError("refusing linked or non-regular engine log")
            os.fchmod(fd, 0o600)
            if st.st_size > self.config.max_log_bytes:
                os.ftruncate(fd, 0)
                self.log_truncations += 1
            return os.fdopen(fd, "ab", buffering=0)
        except Exception:
            os.close(fd)
            raise

    def _enforce_log_limit(self):
        if self.log_handle is None:
            return
        try:
            fd = self.log_handle.fileno()
            size = os.fstat(fd).st_size
            if size >= self.config.max_log_bytes:
                # A live subprocess retains the same O_APPEND descriptor.
                # Truncation is intentional: logs are expendable; .pcap files
                # and the durable events database are never deleted by this.
                os.ftruncate(fd, 0)
                self.log_truncations += 1
                _LOG.info("engine log reached size limit; reset live log")
            self.log_error = ""
        except OSError as error:
            message = f"engine log size enforcement failed: {type(error).__name__}"
            if self.log_error != message:
                _LOG.warning("%s", message)
            self.log_error = message

    def _schedule_retry(self, now):
        """Exponential backoff for repeated engine failures, capped at 5 min.

        A healthy launch resets the streak in _connect. This only applies to
        failures; the normal 1s polling loop remains unchanged.
        """
        self.failure_streak = min(99, self.failure_streak + 1)
        self.retry_delay = (float(self.config.retry_seconds)
                            if self.failure_streak == 1
                            else min(300.0, max(float(self.config.retry_seconds),
                                                self.retry_delay * 2)))
        self.next_try = now + self.retry_delay

    def _launch(self, iface):
        # Never start Bettercap with an unconfigured/blocked regulatory domain.
        # This safety gate runs independently of the boot one-shot so a failed
        # service cannot result in a falsely "running" radio. Doctor stays up.
        if self.config.require_regulatory_country:
            country = read_country(self.config.regulatory_country_file)
            verify_country(country, runner=self.executor)
        if self.lease_guard is not None:
            self.lease_guard.acquire(iface)
            self._save_lease_interlock(False)
            self._check_usb_lease()  # USB mode never retunes or acquires an NM-owned device.
        else:
            # Turnkey (require_usb_lease=false, the default): bring ANY capture
            # radio — onboard OR a USB adapter — to monitor through the same
            # guarded path, so a plugged Alfa upgrades capability instead of being
            # ignored. ensure_monitor still refuses a CONNECTED uplink, which is
            # the real protection against tearing down the box's own link. The
            # full field-provenance USB lease stays available via require_usb_lease=true.
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
        # The subprocess keeps an O_APPEND descriptor, so ftruncate from the
        # supervisor starts a new log segment without a child restart.
        # A restart-only rename previously let a healthy process fill the SD.
        self.log_handle = self._open_engine_log()
        self.engine_attempts += 1
        self._check_usb_lease()
        self.process = self.spawn(
            [self.config.bettercap_binary, "-iface", iface, "-caplet", str(caplet),
             "-no-history", "-env-file", "", "-no-colors", "-silent"],
            # Bettercap always enters an interactive read loop after its caplet.
            # DEVNULL delivers immediate EOF and triggers a recurring 'quit?'
            # prompt even with -silent and -no-history. Hold a private pipe
            # open until our supervisor has reaped the child.
            stdin=subprocess.PIPE, stdout=self.log_handle, stderr=subprocess.STDOUT,
            # Bettercap expands ~/bettercap.aliases from HOME. The hardened
            # systemd unit deliberately blocks /root: give the child only its
            # existing root-owned writable REDUXCAP live state as a home.
            env={**os.environ, "HOME": str(self.config.state_dir)},
            start_new_session=True,
        )
        self.iface = iface
        self.started = self.clock()
        self.state = "starting_engine"
        self._check_usb_lease()

    def _connect(self):
        self._check_usb_lease()
        self.transport.session()  # authenticated readiness probe
        self._check_usb_lease()
        driver = LiveDriver(config=self.transport.config, transport=self.transport,
                            operation_guard=self._check_usb_lease,
                            capture_iface=self.iface if self.lease_guard is not None else None)
        driver.set_handshake_file(str(self.capture_file))
        radios = [r for r in self.radio_probe() if r.iface == self.iface]
        if not radios:
            raise RuntimeError("capture radio disappeared during startup")
        # Supervisor -> BettercapDriver is the exclusive active radio owner.
        store = SightingStore(self.config.state_dir / "sightings.db")
        # Load the persisted central Scope so the daemon honours `redux scope add`
        # and `redux scope lab-mode on`: this arms the driver's deauth allowlist
        # (and, in lab mode, enables Augur's auto-offense). Missing/unreadable scope
        # => an empty, non-lab scope => fully passive, exactly as before.
        from .scope import Scope, default_scope_path
        try:
            scope = Scope.load(default_scope_path())
        except Exception:
            scope = Scope()
        # Retire APs that already gave up a verified 4-way (or that the operator
        # ignores), so lab-mode offense doesn't re-pwn a done target. Fails safe.
        from ..crack.captured import retired_bssids
        try:
            agent = Augur(radios=radios, intent=Intent.RECON, driver=driver, store=store,
                          scope=scope, resource_monitor=self.resources,
                          position_provider=_gps_from_env(),
                          retired_provider=lambda: retired_bssids())
        except Exception:
            store.close()
            raise
        agent.bus.on(Signal.EVENT, self._observe)
        _arm_sentinel_from_env(agent)
        self.driver = driver
        self.augur = agent
        self._cached_visual = None
        self._visual_dirty = True
        self._visual_next_try = float("-inf")
        self._visual_sampled_utc = None
        self._cached_processing = None
        self.state = "running"
        self.last_error = ""
        self.failure_streak = 0
        self.retry_delay = 0.0
        self.next_try = 0.0
        _LOG.info("live passive capture started on %s", self.iface)

    def _stop_engine_child(self):
        """Confirm that Bettercap has stopped before publishing its capture.

        A failed signal or second wait timeout does not authorize handing off a
        still-open .pcap or starting a second engine on the same radio.
        """
        child = self.process
        if child is None:
            return True
        try:
            if child.poll() is None:
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass  # child died between poll and terminate; still reap it
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass
                    child.wait(timeout=2)
            else:
                child.wait(timeout=0)
        except (OSError, subprocess.TimeoutExpired) as error:
            self.state = "termination_pending"
            self.last_error = (
                f"Bettercap termination not confirmed: {type(error).__name__}"
            )
            _LOG.error("%s", self.last_error)
            return False
        # Release the parent's write descriptor only after the child is
        # confirmed exited. Closing it earlier generates interactive EOF.
        input_pipe = getattr(child, "stdin", None)
        if input_pipe is not None:
            try:
                input_pipe.close()
            except OSError:
                pass
        self.process = None
        return True

    def _drop(self):
        # Stop the writer FIRST. Until it has exited, do not close the
        # logger/SQLite resources, publish a .pcap, or forget its PID.
        if not self._stop_engine_child():
            return False
        if self.augur is not None:
            try:
                # One last durable flush before releasing the SQLite connection.
                # Failed data remains in memory until this final attempt. When
                # storage truly cannot recover, report exactly how many queued
                # observations were lost rather than pretending they were saved.
                self.augur.flush_sightings()
            except Exception as error:
                pending = len(self.augur._sighting_buffer)
                self.sighting_loss_events += pending
                self.last_sighting_loss_reason = (
                    f"close with {pending} uncommitted sightings: {type(error).__name__}"
                )
                _LOG.error("%s", self.last_sighting_loss_reason)
            try:
                self.augur.store.close()
            except OSError as error:
                _LOG.error("sighting store close failed: %s", type(error).__name__)
            except Exception as error:
                _LOG.error("sighting store close failed: %s", type(error).__name__)
        self.augur = None
        self.driver = None
        self._cached_visual = None
        self._visual_sampled_utc = None
        self._cached_processing = None
        if self.log_handle is not None:
            try:
                self.log_handle.close()
            except OSError as error:
                self.log_error = f"engine log close failed: {type(error).__name__}"
            finally:
                self.log_handle = None
        self.iface = None
        if self.capture_file is not None:
            queued = self._handoff(self.capture_file)
            if self.lease_guard is not None and not queued:
                try:
                    if self.capture_file.is_file() and self.capture_file.stat().st_size > 0:
                        self._save_lease_interlock(True)
                except (OSError, LeaseLost):
                    self._lease_interlock = self.lease_guard.blocked = True
        self.capture_file = None
        if self.lease_guard is not None:
            self.lease_guard.release()
        return True

    def _handoff(self, source):
        """Durably queue a closed capture without overwriting existing captures.

        Link first, sync the queued directory, then unlink the active name.
        An interrupted handoff may leave *both* names for one inode; the next
        recovery pass recognizes and completes that harmless intermediate state.
        """
        source = Path(source)
        if not self._capture_mount_ok() or self.capture_mount_lost:
            self.last_handoff_error = "REDUXCAP unavailable; capture handoff deferred"
            return False
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
        """Resume stale captures after abrupt power loss; never touch fresh files.

        Filesystem faults can interrupt directory enumeration itself, not just
        individual stat calls. Do not allow that to kill the radio supervisor.
        """
        try:
            for path in self.config.active_dir.iterdir():
                try:
                    if (path.suffix != ".pcap" or path == self.capture_file
                            or path.is_symlink() or not path.is_file()):
                        continue
                    if time.time() - path.stat().st_mtime >= 60:
                        self._handoff(path)
                except OSError:
                    # Concurrent deletion or a bad inode is retryable.
                    continue
        except OSError as error:
            self.last_handoff_error = (
                f"capture recovery scan failed: {type(error).__name__}"
            )
            _LOG.warning("%s", self.last_handoff_error)

    def tick(self):
        self.resources.sample()
        if (self.config.enable_native_plugins and self.native_plugins is None
                and self.clock() >= self.native_next_try):
            try:
                from .signals import SignalBus
                from ..packs.runtime import NativeRuntime
                owner = NativeRuntime(SignalBus(), clock=self.clock)
                owner.start()
                self.native_plugins = owner
                self.observer_errors.pop("plugins", None)
            except Exception:
                # Optional plugin startup must not become a capture failure.
                self.observer_errors['plugins'] = 'native owner startup failed'
                self.native_next_try = self.clock() + 30
        if self.native_plugins is not None:
            try:
                self.native_plugins.bus.emit(Signal.RESOURCE_OBSERVATION,
                                             observation=self.resources.status())
                self.observer_errors.pop("plugins", None)
            except Exception:
                self.observer_errors['plugins'] = 'resource publication failed'

        now = self.clock()
        # Bring up read-only diagnostics even without a radio, a working
        # Bettercap, OR a mounted capture partition. Broken captures must not
        # make their own cause invisible: the loopback Doctor serves the
        # storage_paused finding below from memory and never writes to the root
        # filesystem, so this runs BEFORE the capture-mount guard returns.
        self._maintain_web(now)
        # A forced/lazy unmount invalidates the original flock/partition
        # identity. Never resume on a newly mounted filesystem under a stale
        # lock: a service restart must re-establish exclusive ownership.
        if not self._capture_mount_ok():
            self.capture_mount_lost = True
        if self.capture_mount_lost:
            if (self.process is not None or self.augur is not None) and not self._drop():
                self._checkpoint()
                return self.state
            self.state = "storage_paused"
            self.last_error = (
                "REDUXCAP partition unavailable; restart redux-live.service "
                "after remount to reacquire the ownership lock"
            )
            self._checkpoint()  # in-memory only; never create a rootfs fallback
            return self.state
        self._enforce_log_limit()
        if now - self.last_recovery >= 20:
            self._recover_abandoned()
            self.last_recovery = now
        if self.state == "termination_pending":
            if self._drop():
                self.state = "degraded"
                self._schedule_retry(now)
            self._checkpoint()
            return self.state
        if self.lease_guard is not None:
            try:
                if self.process is not None:
                    self._check_usb_lease()
                if self._lease_interlock or self.lease_guard.blocked:
                    raise LeaseLost('USB capture interlocked')
            except Exception:
                try:
                    self._save_lease_interlock(True)
                except LeaseLost:
                    pass
                if self._drop():
                    self.state = 'lease_blocked'
                    self.last_error = 'USB lease lost; explicit re-arm and fresh qualification required'
                self._checkpoint()
                return self.state
        if self.process is not None and self.process.poll() is not None:
            self.last_error = "Bettercap exited unexpectedly"
            if not self._drop():
                self._checkpoint()
                return self.state
            self.state = "degraded"
            self._schedule_retry(now)

        if self.augur is not None:
            try:
                if free_bytes(self.config.active_dir) < self.config.min_free_bytes:
                    if not self._drop():
                        self._checkpoint()
                        return self.state
                    self.state = "storage_paused"
                    self.last_error = "capture partition is below free-space reserve"
                    self._schedule_retry(now)
                    self._checkpoint()
                    return self.state
                if now - self.started >= self.config.rotation_seconds:
                    if not self._drop():
                        self._checkpoint()
                        return self.state
                    self.state = "rotating"
                    self.next_try = now + 1
                    self._checkpoint()
                    return self.state
                if self.state == "telemetry_degraded":
                    # Retry the previously buffered batch before fetching new
                    # Bettercap events, preventing an unbounded in-memory queue.
                    if now < self.telemetry_next_try:
                        self._checkpoint()
                        return self.state
                    self.augur.flush_sightings()
                self.augur.pump()
                if self.state == "telemetry_degraded":
                    self.state = "running"
                    self.last_error = ""
                if self.lease_guard is None and now - self.last_probe >= self.config.probe_seconds:
                    radio = choose_safe_radio(self.radio_probe(), self.config.preferred_iface,
                                              run=self.executor,
                                              allow_connected=self.config.allow_connected_capture)
                    if radio != self.iface:
                        raise RuntimeError("capture radio removed or reassigned")
                    self.last_probe = now
            except sqlite3.Error as error:
                # Keep the Bettercap owner and uncommitted Augur queue alive:
                # a brief SQLite lock or media hiccup need not lose telemetry.
                self.last_error = (
                    f"sighting persistence failed: {type(error).__name__}: "
                    f"{str(error)[:110]}"
                )
                self.state = "telemetry_degraded"
                self.telemetry_next_try = now + self.config.retry_seconds
                _LOG.warning("%s", self.last_error)
            except Exception as error:
                self.last_error = f"live polling stopped: {str(error)[:150]}"
                _LOG.warning("%s", self.last_error)
                if not self._drop():
                    self._checkpoint()
                    return self.state
                self.state = "degraded"
                self._schedule_retry(now)
        elif self.process is not None:
            try:
                self._connect()
            except Exception as error:
                # Preserve the actual handshake error during the startup
                # grace period instead of showing only "starting_engine".
                # Never print an API response body or caplet secrets.
                self.last_error = f"engine initialization: {str(error)[:150]}"
                if now - self.started > 15:
                    if not self._drop():
                        self._checkpoint()
                        return self.state
                    self.state = "degraded"
                    self._schedule_retry(now)
        elif now >= self.next_try:
            try:
                if free_bytes(self.config.active_dir) < self.config.min_free_bytes:
                    raise RuntimeError("capture partition is below free-space reserve")
                iface = (self.config.preferred_iface if self.lease_guard is not None else
                         choose_safe_radio(self.radio_probe(), self.config.preferred_iface,
                                           run=self.executor,
                                           allow_connected=self.config.allow_connected_capture))
                if not iface:
                    raise RuntimeError("no monitor-capable Wi-Fi interface detected")
                self._launch(iface)
            except Exception as error:
                if self.lease_guard is not None and self.lease_guard.blocked:
                    try:
                        self._save_lease_interlock(True)
                    except LeaseLost:
                        pass
                self.last_error = f"engine launch: {str(error)[:150]}"
                _LOG.warning("%s", self.last_error)
                if not self._drop():
                    self._checkpoint()
                    return self.state
                self.state = "degraded"
                self._schedule_retry(now)
        self._checkpoint()
        return self.state

    def close(self):
        if self._lock.closed:
            return
        # Even on stop failure, release the Python supervisor's resources and
        # exit non-zero. systemd's default KillMode=control-group then retains
        # responsibility for any surviving service child before a new start.
        self._closing = True
        if self.native_plugins is not None:
            try:
                self.native_plugins.close()
            except Exception:
                self.observer_errors['plugins'] = 'native shutdown failed'
                _LOG.warning('Optional native runtime shutdown failed')
        stopped = self._drop()
        try:
            if self.web is not None:
                self.web.shutdown()
                self.web.server_close()
                self.web = None
            self.state = "stopped" if stopped else "termination_pending"
            self._checkpoint()
        finally:
            for worker in (self.web_thread, self._radio_inventory_thread):
                join = getattr(worker, "join", None)
                if callable(join) and worker is not threading.current_thread():
                    try:
                        join(timeout=0.25)
                    except RuntimeError:
                        pass  # an observer thread may have failed before start
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._lock.close()
        if not stopped:
            raise RuntimeError("Bettercap child remained alive during Redux shutdown")


def preflight(config, *, which=shutil.which, radio_probe=probe,
              radio_run=subprocess.run, is_mount=os.path.ismount,
              available_bytes=free_bytes, lease_guard=None):
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
        if config.require_usb_lease:
            guard = lease_guard or UsbLeaseGuard(FileLeaseEvidence(config.usb_evidence_file),
                KernelUsbVerifier(config.usb_management_peer, run=radio_run))
            iface = guard.acquire(config.preferred_iface).iface
            guard.check()
            guard.release()
            checks['usb_lease'] = True
        else:
            iface = choose_safe_radio(ifaces, config.preferred_iface,
                                      run=radio_run,
                                      allow_connected=config.allow_connected_capture)
        if iface and not config.require_usb_lease and usb_attached(iface):
            errors.append("USB capture requires explicit lease configuration")
        checks["capture_interface_found"] = bool(iface)
        if not iface:
            errors.append("no safe monitor-capable capture radio detected (connected or unavailable)")
    except (OSError, RuntimeError, ValueError) as error:
        iface = None
        checks["capture_interface_found"] = False
        errors.append(f"radio probe failed: {type(error).__name__}")
    return {"ready": not errors, "errors": errors, "warnings": warnings,
            "capture_radio": iface, "checks": checks}


def _storage_unavailable(error):
    """True when a runtime could not be built because REDUXCAP is not mounted.

    Any other construction error is a real fault we must not paper over with a
    diagnostics stub — it propagates so systemd surfaces it."""
    return (isinstance(error, RuntimeError)
            and "REDUXCAP" in str(error)
            and "not mounted" in str(error))


def degraded_storage_snapshot(now_utc=None):
    """A valid, honest /api/status snapshot for the pre-mount window.

    The capture partition is not mounted yet, so no LiveRuntime exists. This
    serves the SAME contract the dashboard and fbui expect — a runtime block and
    a doctor block — reporting storage as the action item, so Doctor reads
    'ACTION REQUIRED: capture storage unavailable' instead of being unreachable.
    It is built entirely in memory and never writes to any filesystem."""
    now_utc = time.time() if now_utc is None else now_utc
    health = describe_live_health(
        "storage_paused", capture_mount_ok=False,
        last_error=("REDUXCAP ext4 partition is not mounted at /captures; "
                    "the capture supervisor is waiting for it to appear"),
        dashboard_enabled=True, dashboard_active=True,
    )
    return {
        "runtime": {"state": "storage_paused", "updated_utc": now_utc,
                    "iface": "", "capture_mount": False,
                    "last_error": "REDUXCAP partition is not mounted at /captures",
                    "dashboard_active": True},
        "doctor": health,
        "telemetry": None,
        "governor": {},
        "capture_processing": {"available": False,
                               "reason": "REDUXCAP mount unavailable"},
    }


def _serve_degraded_diagnostics(config):
    """Start a loopback read-only diagnostics server for the pre-mount window.

    Returns the ThreadingHTTPServer (or None if the web dashboard is disabled or
    the port could not be bound — e.g. a lingering listener). Serves a fresh
    storage-paused snapshot per request; writes nothing anywhere."""
    if not config.enable_web:
        return None
    try:
        server = ThreadingHTTPServer(
            ("127.0.0.1", config.web_port),
            make_handler(lambda: degraded_storage_snapshot(), token=None))
    except OSError as error:
        _LOG.warning("degraded diagnostics bind failed: %s", type(error).__name__)
        return None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    try:
        thread.start()
    except Exception:
        server.server_close()
        return None
    _LOG.warning("REDUXCAP not mounted; serving storage-paused Doctor on "
                 "127.0.0.1:%s until the capture partition appears", config.web_port)
    return server


def run_forever(config, *, stop=None, runtime_factory=LiveRuntime, interval=1.0,
                mount_retry_seconds=5.0):
    from threading import Event
    signal_stop = stop or Event()
    if interval <= 0:
        raise ValueError("interval must be positive")
    # Build the runtime. If the only problem is that REDUXCAP is not mounted
    # yet, do NOT crash-loop the service (which would leave the operator with a
    # dead screen and an unreachable Doctor). Instead serve a storage-paused
    # Doctor on loopback and keep retrying construction until the mount appears.
    runtime = None
    degraded = None
    try:
        while runtime is None and not signal_stop.is_set():
            try:
                runtime = runtime_factory(config)
            except Exception as error:
                if not _storage_unavailable(error):
                    raise
                if degraded is None:
                    degraded = _serve_degraded_diagnostics(config)
                signal_stop.wait(mount_retry_seconds)
        if runtime is None:
            return
        # Hand off the loopback port to the real runtime before it starts its own.
        if degraded is not None:
            degraded.shutdown()
            degraded.server_close()
            degraded = None
        while not signal_stop.is_set():
            runtime.tick()
            signal_stop.wait(interval)
    finally:
        if degraded is not None:
            degraded.shutdown()
            degraded.server_close()
        if runtime is not None:
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
    def runtime_factory(settings):
        # Explicitly provision the optional read-only collector only for the
        # installed service entrypoint. Unit tests and alternate embeddings do
        # not start shell/sysfs probes unless they inject this provider.
        from ..radio.display_inventory import collect
        return LiveRuntime(settings, radio_inventory_provider=collect)

    run_forever(config, stop=shutdown, runtime_factory=runtime_factory)


if __name__ == "__main__":
    main()
