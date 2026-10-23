"""Opt-in, passive AngryOxide companion to an ALREADY supervised Bettercap.

No service imports or starts this module. The caller must explicitly authorize
each start, supply a currently measured tandem candidate and maintain the
Bettercap owner's lifecycle. The USB guard and process evidence are independent
of the planner. Never changes a radio, NetworkManager, or the Ethernet route.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile

from ..crack.capture import AngryOxideConfig, AngryOxideProvider
from ..radio.live_lease import (KernelUsbVerifier, LeaseLost, ProcessOwner,
                                UsbLeaseGuard)
from ..storage_mount import capture_mount_snapshot
from .tandem_readiness import TandemReadiness


_INGEST_SUFFIXES = frozenset({'.pcap', '.pcapng', '.cap', '.hc22000'})


def tandem_usb_guard(observe, management_peer, bettercap_owner: ProcessOwner, *,
                     clock, run=subprocess.run, sys_root=Path('/sys/class/net'),
                     proc_root=Path('/proc')) -> UsbLeaseGuard:
    """Production-shaped strict guard: allow exactly the pinned onboard peer.

    The caller must first pin the Bettercap child it already owns, using
    ``ProcessOwner.pin(child.pid, 'bettercap', onboard_iface)``. Neither a
    running daemon found by name nor a readiness preview confers ownership.
    """
    if not isinstance(bettercap_owner, ProcessOwner):
        raise ValueError('verified Bettercap owner required')
    kernel = KernelUsbVerifier(management_peer, run=run, sys_root=sys_root,
                               proc_root=proc_root, owner_engine='angryoxide',
                               tandem_peer=bettercap_owner)
    return UsbLeaseGuard(observe, kernel, clock=clock)


@dataclass(frozen=True)
class CompanionPaths:
    root: Path = Path('/captures')

    @property
    def active(self):
        return self.root / 'active'

    @property
    def incoming(self):
        return self.root / 'incoming'

    @property
    def state(self):
        return self.root / 'live'


class TandemCompanion:
    """Explicit one-shot owner; never autostarts or silently rearms a USB radio.

    ``tick`` must be called throughout the owning Bettercap runtime's lifetime;
    on ANY lost peer/lease/mount it stops AngryOxide and latches. Capture output
    is published only after confirmed child exit. A failed reap or handoff
    cannot release the lease and cannot launch another process.
    """

    def __init__(self, readiness: TandemReadiness, bettercap_child, bettercap_owner: ProcessOwner,
                 usb_guard: UsbLeaseGuard, *, paths=CompanionPaths(),
                 spawn=subprocess.Popen, mount_probe=capture_mount_snapshot,
                 proc_root=Path('/proc'), binary='angryoxide'):
        if (not isinstance(readiness, TandemReadiness) or readiness.state != 'radio_qualified'
                or readiness.activation_authorized is not False
                or len(readiness.bindings) != 2 or readiness.usb_lease is None):
            raise LeaseLost('radio candidate not independently qualified')
        bindings = {binding.engine: binding for binding in readiness.bindings}
        if (set(bindings) != {'bettercap', 'angryoxide'}
                or any(not binding.passive for binding in readiness.bindings)
                or bindings['bettercap'].iface != bettercap_owner.iface
                or bindings['bettercap'].phy == bindings['angryoxide'].phy
                or bindings['bettercap'].ifindex == bindings['angryoxide'].ifindex
                or (readiness.usb_lease.iface, readiness.usb_lease.phy,
                    readiness.usb_lease.ifindex) !=
                   (bindings['angryoxide'].iface, bindings['angryoxide'].phy,
                    bindings['angryoxide'].ifindex)
                or getattr(bettercap_child, 'pid', None) != bettercap_owner.pid):
            raise LeaseLost('engine/radio/process identities conflict')
        if not isinstance(paths, CompanionPaths) or not paths.root.is_absolute():
            raise ValueError('absolute capture storage root required')
        if (not isinstance(usb_guard, UsbLeaseGuard)
                or not isinstance(bettercap_owner, ProcessOwner)
                or bettercap_owner.engine != 'bettercap'):
            raise LeaseLost('independent capture owners required')
        self.readiness = readiness
        self.bettercap_child = bettercap_child
        self.bettercap_owner = bettercap_owner
        self.usb_guard = usb_guard
        self.paths = paths
        self.spawn = spawn
        self.mount_probe = mount_probe
        self.proc_root = Path(proc_root)
        self.binary = binary
        self.mount_identity = None
        self.process = None
        self.log_handle = None
        self.output_dir = None
        self.state = 'idle'
        self.last_error = ''
        self.handoffs = 0

    def _mount_ok(self):
        identity = self.mount_probe()
        return identity is not None and (self.mount_identity is None or identity == self.mount_identity)

    def _peer_ok(self):
        if self.bettercap_child.poll() is not None:
            raise LeaseLost('Bettercap owner is not running')
        self.bettercap_owner.verify(proc_root=self.proc_root)

    def _storage_ok(self):
        if not self._mount_ok():
            raise LeaseLost('REDUXCAP mount unavailable or replaced')
        if not self.paths.root.is_dir() or self.paths.root.is_symlink():
            raise LeaseLost('capture root invalid')
        device = self.paths.root.stat().st_dev
        for path in (self.paths.state, self.paths.active, self.paths.incoming):
            if not path.is_dir() or path.is_symlink() or path.stat().st_dev != device:
                raise LeaseLost('capture directories missing, symlinked or on another filesystem')
        if self.output_dir is not None and (not self.output_dir.is_dir()
                                            or self.output_dir.is_symlink()
                                            or self.output_dir.stat().st_dev != device):
            raise LeaseLost('active capture output directory changed')

    def start(self, *, operator_authorized=False):
        if operator_authorized is not True:
            raise LeaseLost('explicit operator authorization required to launch')
        if self.state != 'idle' or self.process is not None or self.output_dir is not None:
            raise LeaseLost('companion cannot restart without explicit re-arm')
        initial_mount = self.mount_probe()
        if initial_mount is None:
            raise LeaseLost('REDUXCAP mount not verified')
        self.mount_identity = initial_mount
        self._storage_ok()
        self._peer_ok()
        iface = self.readiness.usb_lease.iface
        try:
            if self.usb_guard.acquire(iface) != self.readiness.usb_lease:
                raise LeaseLost('fresh USB lease does not match measured tandem generation')
            self._peer_ok()
            self._storage_ok()
            self.output_dir = Path(tempfile.mkdtemp(prefix='angryoxide-', dir=self.paths.active))
            provider = AngryOxideProvider(config=AngryOxideConfig(
                binary=self.binary, iface=iface, out_dir=str(self.output_dir),
                autoexit=False, autohunt=False))
            if not provider.available():
                raise LeaseLost('AngryOxide binary unavailable')
            plan = provider.plan(None, active=False, iface=iface)
            if (not plan.runnable or not plan.passive or plan.targets
                    or '--notransmit' not in plan.argv or '-t' in plan.argv):
                raise LeaseLost('AngryOxide invocation not passive')
            logfile = self.paths.state / (self.output_dir.name + '.log')
            fd = os.open(logfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            self.log_handle = os.fdopen(fd, 'wb')
            self.usb_guard.check(owner_pid=None)
            self.process = self.spawn(plan.argv, stdin=subprocess.DEVNULL,
                                      stdout=self.log_handle, stderr=subprocess.STDOUT,
                                      start_new_session=True)
            if (type(getattr(self.process, 'pid', None)) is not int
                    or self.process.pid <= 0 or self.process.poll() is not None):
                raise LeaseLost('AngryOxide failed to enter supervised running state')
            self._peer_ok()
            self.usb_guard.check(owner_pid=self.process.pid)
            self._storage_ok()
            self.state = 'running'
            return self.state
        except Exception as error:
            self.last_error = f'AngryOxide startup blocked: {type(error).__name__}'
            self.usb_guard.blocked = True
            if not self.stop():
                self.state = 'termination_pending'
            raise LeaseLost(self.last_error) from None

    def tick(self):
        if self.state == 'termination_pending':
            if self.stop() and self.usb_guard.blocked:
                self.state = 'interlocked'
            return self.state
        if self.state != 'running':
            return self.state
        try:
            self._peer_ok()
            self._storage_ok()
            if self.process.poll() is not None:
                raise LeaseLost('AngryOxide exited unexpectedly')
            self.usb_guard.check(owner_pid=self.process.pid)
        except Exception as error:
            self.last_error = f'tandem continuity lost: {type(error).__name__}'
            self.usb_guard.blocked = True
            self.stop()
            if self.state == 'stopped':
                self.state = 'interlocked'
        return self.state

    def _reap(self):
        child = self.process
        if child is None:
            return True
        try:
            if child.poll() is None:
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass
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
        except (OSError, subprocess.TimeoutExpired):
            self.state = 'termination_pending'
            return False
        self.process = None
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None
        return True

    @staticmethod
    def _sync_dir(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _handoff(self):
        if self.output_dir is None:
            return True
        self._storage_ok()
        for source in self.output_dir.iterdir():
            # Non-ingest metadata stays at rest for inspection. Never follow a
            # symlink or traverse nested output supplied by the child.
            if source.suffix.lower() not in _INGEST_SUFFIXES:
                continue
            info = source.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise LeaseLost('AngryOxide output is not a regular file')
            if info.st_size == 0:
                continue
            target = self.paths.incoming / (self.output_dir.name + '-' + source.name)
            if target.exists() or target.is_symlink():
                raise LeaseLost('capture handoff destination already exists')
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                current = os.fstat(fd)
                if (current.st_dev, current.st_ino, current.st_size) != (
                        info.st_dev, info.st_ino, info.st_size):
                    raise LeaseLost('capture changed before handoff')
                os.fsync(fd)
            finally:
                os.close(fd)
            os.link(source, target, follow_symlinks=False)
            linked = target.lstat()
            still_source = source.lstat()
            if (not stat.S_ISREG(linked.st_mode)
                    or (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino)
                    or (still_source.st_dev, still_source.st_ino) != (info.st_dev, info.st_ino)):
                raise LeaseLost('capture path changed during handoff')
            self._sync_dir(self.paths.incoming)
            source.unlink()
            self._sync_dir(self.output_dir)
            self.handoffs += 1
        return True

    def stop(self):
        if not self._reap():
            return False
        try:
            self._handoff()
        except (OSError, LeaseLost) as error:
            self.state = 'handoff_blocked'
            self.last_error = f'closed AngryOxide captures retained: {type(error).__name__}'
            self.usb_guard.blocked = True
            return False
        if self.usb_guard.lease is not None:
            self.usb_guard.release()
        if self.state == 'handoff_blocked':
            self.state = 'interlocked'
        elif self.state != 'interlocked':
            self.state = 'stopped'
        return True

    def rearm(self, *, operator_authorized=False):
        if (operator_authorized is not True or self.process is not None
                or self.state not in ('stopped', 'interlocked')):
            raise LeaseLost('re-arm requires explicit operator approval and fully stopped writer')
        self._storage_ok()
        self.usb_guard.rearm(owner_authorized=True)
        self.output_dir = None
        self.mount_identity = None
        self.state = 'idle'
        self.last_error = ''
