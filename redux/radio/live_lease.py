"""Fail-closed USB capture interlock for the live Bettercap owner.

Evidence files are local root-owned collector output, not user authorization.
Fresh receipts never replace independent kernel/NM/process/route checks. No
function changes interface modes, NM ownership, services or management routes.
"""
from dataclasses import dataclass
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from .dual_radio import RadioEvidence
from .orchestrator import Radio
from .split_integration import MeasuredRadio, capture_lease, lease_present, plan_measured_split


def usb_attached(iface, *, sys_root=Path('/sys/class/net')):
    """Positive read-only USB detection; missing devices cannot pass iw readiness."""
    if not isinstance(iface, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,15}', iface):
        raise ValueError('invalid radio interface')
    try:
        device = (Path(sys_root)/iface/'device').resolve(strict=True)
    except FileNotFoundError:
        return False
    return any((parent/'subsystem').is_symlink() and (parent/'subsystem').resolve().name == 'usb'
               for parent in (device, *device.parents))


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessOwner:
    """A pinned Linux process incarnation AND its exact capture interface.

    A PID or process name on its own is not an ownership proof: PID reuse,
    process replacement, and a second interface argument must fail closed.
    """
    pid: int
    engine: str
    iface: str
    start_ticks: int

    @classmethod
    def pin(cls, pid, engine, iface, *, proc_root=Path('/proc')):
        if (type(pid) is not int or pid <= 0 or engine not in ('bettercap', 'angryoxide')
                or not isinstance(iface, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,15}', iface)):
            raise LeaseLost('invalid process owner identity')
        owner = cls(pid, engine, iface, 0)
        ticks = owner._inspect(Path(proc_root))
        return cls(pid, engine, iface, ticks)

    def verify(self, *, proc_root=Path('/proc')):
        if self._inspect(Path(proc_root)) != self.start_ticks:
            raise LeaseLost('capture process incarnation changed')

    def _inspect(self, proc_root):
        try:
            directory = proc_root / str(self.pid)
            if (directory / 'comm').read_text().strip() != self.engine:
                raise LeaseLost('capture process name changed')
            argv = (directory / 'cmdline').read_bytes()[:16385]
            if len(argv) > 16384 or not argv.endswith(b'\x00'):
                raise LeaseLost('capture command line unverified')
            parts = [x.decode('utf-8') for x in argv[:-1].split(b'\x00')]
            flag = '-iface' if self.engine == 'bettercap' else '-i'
            positions = [i for i, arg in enumerate(parts) if arg == flag]
            if (not parts or Path(parts[0]).name != self.engine or len(positions) != 1
                    or positions[0] + 1 >= len(parts) or parts[positions[0] + 1] != self.iface):
                raise LeaseLost('capture interface arguments changed')
            # proc stat field 22 (starttime); field 2 is parenthesized and
            # may itself contain spaces or parentheses.
            stat_row = (directory / 'stat').read_text()
            fields = stat_row.rsplit(') ', 1)[1].split()
            ticks = int(fields[19])
            if ticks <= 0:
                raise LeaseLost('capture process starttime unverified')
            return ticks
        except (OSError, UnicodeError, ValueError, IndexError):
            raise LeaseLost('capture process identity unavailable') from None


@dataclass(frozen=True)
class LeaseObservation:
    radios: tuple[MeasuredRadio, ...]
    sampled_at: float
    ethernet_verified: bool = False


def validate_receipt(d, boot_id):
    """Strict local transport schema; qualification still requires live checks."""
    if (type(d) is not dict or d.get('boot_id') != boot_id
            or type(d.get('ethernet_verified')) is not bool
            or type(d.get('sampled_monotonic')) not in (int, float)
            or not 0 <= d['sampled_monotonic'] <= 1e12 or not math.isfinite(d['sampled_monotonic'])
            or type(d.get('radios')) is not list or not 1 <= len(d['radios']) <= 32):
        raise ValueError('invalid receipt')
    names, indices = set(), set()
    for row in d['radios']:
        if (type(row) is not dict
                or not isinstance(row.get('iface'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,15}', row['iface'])
                or not isinstance(row.get('phy'), str) or not re.fullmatch(r'phy[0-9]+', row['phy'])
                or type(row.get('ifindex')) is not int or row['ifindex'] <= 0
                or type(row.get('monitor')) is not bool
                or type(row.get('owner_verified')) is not bool
                or row.get('mode') not in ('monitor', 'managed', 'unknown')
                or row.get('transport') not in ('usb', 'onboard', 'unknown')
                or row.get('owner') not in ('unknown', 'capture_exclusive', 'networkmanager')
                or type(row.get('bands', [])) is not list
                or any(b not in ('2.4', '5', '6') for b in row.get('bands', []))
                or row.get('arphrd') is not None and (type(row['arphrd']) is not int or not 0 < row['arphrd'] <= 65535)
                or row.get('decoded_frames') is not None and (type(row['decoded_frames']) is not int or row['decoded_frames'] < 0)):
            raise ValueError('invalid radio observation')
        required = {'iface', 'phy', 'ifindex', 'monitor', 'mode', 'arphrd',
                    'decoded_frames', 'owner_verified', 'owner', 'transport'}
        if not required <= row.keys() or row['iface'] in names or row['ifindex'] in indices:
            raise ValueError('ambiguous radio identity')
        names.add(row['iface'])
        indices.add(row['ifindex'])
    return d


class FileLeaseEvidence:
    """Bounded, no-follow collector contract. Monotonic age checked by guard."""
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError('lease evidence path must be absolute')

    def __call__(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022
                    or not 1 <= st.st_size <= 65536):
                raise LeaseLost('lease evidence file not trusted')
            raw = os.read(fd, 65537)
        finally:
            os.close(fd)
        try:
            d = json.loads(raw)
            validate_receipt(d, Path('/proc/sys/kernel/random/boot_id').read_text().strip())
            radios = []
            for row in d['radios']:
                r = Radio(row['iface'], phy=row['phy'], monitor=row['monitor'],
                          bands=frozenset(row.get('bands', ())))
                evidence = RadioEvidence(row['phy'], row['mode'], row['arphrd'],
                                         row['decoded_frames'], row['owner_verified'])
                radios.append(MeasuredRadio(r, evidence, row['ifindex'], row['transport'],
                                            True, row['owner']))
            return LeaseObservation(tuple(radios), d['sampled_monotonic'], d.get('ethernet_verified') is True)
        except (KeyError, TypeError, ValueError):
            raise LeaseLost('invalid lease evidence schema') from None


class UsbLeaseGuard:
    """One lease per explicit arm. Loss latches until runtime owner re-arms it."""
    def __init__(self, observe, verify_kernel, *, clock=time.monotonic, max_age=3):
        self.observe = observe
        self.verify_kernel = verify_kernel
        self.clock = clock
        if type(max_age) not in (int, float) or not math.isfinite(max_age) or not 0 < max_age <= 3:
            raise ValueError("USB observation age must be finite and at most three seconds")
        self.max_age = max_age
        self._last_now = float("-inf")
        self.lease = None
        self.expected = None
        self.blocked = False
        self._sampled = float('-inf')

    def _read(self):
        observation = self.observe()
        stamp = observation.sampled_at
        now = self.clock()
        if (type(now) not in (int, float) or not math.isfinite(now) or now < self._last_now
                or type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not 0 <= now - stamp <= self.max_age or stamp < self._sampled
                or observation.ethernet_verified is not True):
            raise LeaseLost('USB ownership or management observation is stale')
        self._sampled = stamp
        self._last_now = now
        return observation

    def acquire(self, iface):
        if self.blocked or self.lease is not None:
            raise LeaseLost('USB lease interlocked; explicit re-arm required')
        try:
            observation = self._read()
            plan = plan_measured_split(observation.radios, ethernet_management_verified=True)
            lease = capture_lease(plan, observation.radios, ethernet_management_verified=True)
            if lease is None or lease.iface != iface:
                raise LeaseLost('requested USB capture is not qualified')
            if self.expected is not None and lease != self.expected:
                raise LeaseLost("USB device generation changed; explicit re-arm required")
            self.verify_kernel(lease, None)
            self.expected = lease
            self.lease = lease
            return lease
        except Exception:
            self.blocked = True
            raise LeaseLost('USB lease acquisition failed') from None

    def check(self, *, owner_pid=None):
        try:
            if self.blocked or self.lease is None:
                raise LeaseLost('USB capture lease absent or interlocked')
            observation = self._read()
            if not lease_present(self.lease, observation.radios, management_verified=True):
                raise LeaseLost('USB capture lease changed')
            self.verify_kernel(self.lease, owner_pid)
        except Exception:
            self.blocked = True
            raise LeaseLost('USB capture ownership/identity/management lost') from None

    def release(self):
        # Only called AFTER old child termination and closed-file handoff.
        self.lease = None
        if hasattr(self.verify_kernel, 'release_owner'):
            self.verify_kernel.release_owner()

    def rearm(self, *, owner_authorized=False):
        if owner_authorized is not True or self.lease is not None:
            raise LeaseLost('explicit re-arm requires a released capture owner')
        self.blocked = False
        self.expected = None
        if hasattr(self.verify_kernel, "net_identity"):
            self.verify_kernel.net_identity = None


class KernelUsbVerifier:
    """Independent read-only invariants; Ethernet management is required here.

    The capture adapter must already be monitor/native and NM explicitly no/10.
    This live integration intentionally never tries to acquire ownership itself.
    """
    def __init__(self, management_peer, *, run=subprocess.run, sys_root=Path('/sys/class/net'),
                 proc_root=Path('/proc'), owner_engine='bettercap', tandem_peer=None):
        if (owner_engine not in ('bettercap', 'angryoxide')
                or (owner_engine == 'angryoxide') != (tandem_peer is not None)
                or (tandem_peer is not None and
                    (not isinstance(tandem_peer, ProcessOwner) or tandem_peer.engine != 'bettercap'))):
            raise ValueError('tandem requires a pinned Bettercap peer and AngryOxide owner')
        self.peer = str(ipaddress.ip_address(management_peer))
        self.run, self.sys_root, self.proc_root = run, Path(sys_root), Path(proc_root)
        self.ethernet_identity = None
        self.net_identity = None
        self.owner_engine = owner_engine
        self.tandem_peer = tandem_peer
        self.owner_identity = None

    def _command(self, *args):
        p = self.run(list(args), capture_output=True, text=True, timeout=2, check=False,
                     env={**os.environ, 'LC_ALL': 'C'})
        if p.returncode or len(p.stdout) > 131072:
            raise LeaseLost('bounded USB invariant probe failed')
        return p.stdout

    def __call__(self, lease, owner_pid):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,15}', lease.iface):
            raise LeaseLost('invalid capture netdev')
        if self.tandem_peer is not None and self.tandem_peer.iface == lease.iface:
            raise LeaseLost('Bettercap and AngryOxide may not share the USB interface')
        routes = json.loads(self._command('ip', '-j', 'route', 'get', self.peer))
        eth = json.loads(self._command('ip', '-j', 'address', 'show', 'dev', 'eth0'))
        if (len(routes) != 1 or routes[0].get('dev') != 'eth0' or len(eth) != 1
                or 'UP' not in eth[0].get('flags', ())):
            raise LeaseLost('verified Ethernet return path unavailable')
        identity = (eth[0]['ifindex'], tuple(sorted(a['local'] for a in eth[0]['addr_info'])))
        self.ethernet_identity = self.ethernet_identity or identity
        if identity != self.ethernet_identity:
            raise LeaseLost('Ethernet identity changed')
        node = self.sys_root / lease.iface
        net_identity = (node.stat().st_dev, node.stat().st_ino)
        if self.net_identity is not None and self.net_identity != net_identity:
            raise LeaseLost('USB netdev generation changed')
        if (int((node / 'ifindex').read_text()) != lease.ifindex
                or int((node / 'type').read_text()) not in (801, 803)
                or not int((node / 'flags').read_text().strip(), 16) & 1):
            raise LeaseLost('USB netdev identity or framing changed')
        device = (node / 'device').resolve(strict=True)
        if not any((p/'subsystem').is_symlink() and (p/'subsystem').resolve().name == 'usb'
                   for p in (device, *device.parents)):
            raise LeaseLost('capture netdev is not measured USB')
        info = self._command('iw', 'dev', lease.iface, 'info')
        if (not re.search(r'^\s*type monitor\s*$', info, re.M)
                or not re.search(r'^\s*wiphy '+re.escape(lease.phy[3:])+r'\s*$', info, re.M)):
            raise LeaseLost('capture mode or PHY changed')
        raw = self._command('iw', 'dev')
        blocks = re.split(r'(?m)^phy#', raw)
        names = []
        for block in blocks[1:]:
            if block.splitlines()[0].strip() == lease.phy[3:]:
                names.extend(re.findall(r'(?m)^\s*Interface (\S+)\s*$', block))
        if names != [lease.iface]:
            raise LeaseLost('capture PHY is not exclusive')
        nm = self._command('nmcli', '-g', 'GENERAL.NM-MANAGED,GENERAL.STATE', 'device', 'show', lease.iface)
        if not re.fullmatch(r'no\n10(?: \([^\n]*\))?\s*', nm):
            raise LeaseLost('NM exclusion not explicit')
        # Explicit managed=no does not independently exclude a supplicant binding.
        raw = self._command('busctl', '--system', '--auto-start=no', 'get-property',
            'fi.w1.wpa_supplicant1', '/fi/w1/wpa_supplicant1', 'fi.w1.wpa_supplicant1', 'Interfaces')
        match = re.fullmatch(r'ao ([0-9]+)((?: "[A-Za-z0-9_/]+")*)\s*', raw)
        if not match or int(match[1]) > 16:
            raise LeaseLost('supplicant inventory unverified')
        paths = re.findall(r'"([^"]+)"', match[2])
        if len(paths) != int(match[1]):
            raise LeaseLost('supplicant inventory invalid')
        for path in paths:
            value = self._command('busctl', '--system', '--auto-start=no', 'get-property',
                'fi.w1.wpa_supplicant1', path, 'fi.w1.wpa_supplicant1.Interface', 'Ifname')
            if not value.startswith('s ') or json.loads(value[2:]) == lease.iface:
                raise LeaseLost('supplicant capture exclusion unverified')
        supplicants = 0
        peer_seen = False
        owner_seen = False
        for directory in self.proc_root.iterdir():
            if not directory.name.isdigit():
                continue
            try:
                comm = (directory/'comm').read_text().strip()
            except FileNotFoundError:
                continue
            if comm == 'wpa_supplicant':
                supplicants += 1
                if '/wpa_supplicant.service' not in (directory/'cgroup').read_text():
                    raise LeaseLost('unowned supplicant instance')
            pid = int(directory.name)
            if (self.tandem_peer is not None and pid == self.tandem_peer.pid
                    and comm == 'bettercap'):
                self.tandem_peer.verify(proc_root=self.proc_root)
                peer_seen = True
                continue
            if self.owner_engine == 'angryoxide' and comm == 'angryoxide' and pid == owner_pid:
                if self.owner_identity is None:
                    self.owner_identity = ProcessOwner.pin(pid, 'angryoxide', lease.iface,
                                                           proc_root=self.proc_root)
                self.owner_identity.verify(proc_root=self.proc_root)
                owner_seen = True
                continue
            if comm in {'hostapd','iwd','tcpdump','dumpcap','tshark','airodump-ng','hcxdumptool','kismet',
                        'angryoxide'}:
                raise LeaseLost('conflicting capture owner')
            if comm == 'bettercap' and (self.owner_engine != 'bettercap' or pid != owner_pid):
                raise LeaseLost('unowned Bettercap instance')
        if (self.tandem_peer is not None and not peer_seen
                or self.owner_engine == 'angryoxide' and owner_pid is not None and not owner_seen):
            raise LeaseLost('pinned tandem engine missing')
        if supplicants != 1 or (Path('/run/wpa_supplicant')/lease.iface).exists():
            raise LeaseLost('supplicant process ownership unverified')
        if (node.stat().st_dev, node.stat().st_ino) != net_identity or int((node/'ifindex').read_text()) != lease.ifindex:
            raise LeaseLost('USB identity changed during observation')
        self.net_identity = net_identity

    def release(self):
        self.net_identity = None
        self.release_owner()

    def release_owner(self):
        self.owner_identity = None
