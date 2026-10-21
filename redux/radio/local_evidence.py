"""Independent read-only observations. No RF sockets, ownership acquisition or retry.

Receipts deliberately cannot qualify reception: decoded_frames is always None.
Private kernel identities stay local; public reports use ordinal radio labels.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import tempfile
import time

from .live_lease import KernelUsbVerifier, LeaseLost, validate_receipt
from .probe import parse_iw_phy
from .split_integration import CaptureLease


class Unavailable(RuntimeError):
    """Only fixed classifications may cross the reporting boundary."""


class BoundedReader:
    """Allowlisted commands, shared deadline, hard combined output bound.

    No shell, inherited stdin, environment overrides, daemon activation or logs.
    Timeout/overflow kills and reaps the one direct read-only child.
    """
    def __init__(self, *, seconds=20, clock=time.monotonic, command_seconds=2):
        if not 0 < command_seconds <= 45 or not 0 < seconds <= 45:
            raise ValueError("bounded command deadlines required")
        self.command_seconds = command_seconds
        self.clock = clock
        self.deadline = clock() + seconds

    @staticmethod
    def allowed(args):
        fixed = {('iw', 'phy'), ('iw', 'dev'), ('iw', 'reg', 'get'),
                 ('uname', '-r'), ('modinfo', '-F', 'vermagic', 'brcmfmac'),
                 ('modinfo', '-F', 'srcversion', 'brcmfmac'),
                 ('findmnt', '-J', '-T', '/captures', '-o', 'TARGET,FSTYPE,OPTIONS'),
                 ('systemctl', 'show', '--property=ActiveState', '--value', 'redux.service'),
                 ('systemctl', 'show', '--property=ActiveState', '--value', 'redux-live.service'),
                 ('systemctl', 'show', '--property=ActiveState', '--value', 'redux-capture-ingest.timer')}
        a = tuple(args)
        if a in fixed:
            return True
        if len(a) == 5 and a[:4] == ('ip', '-j', 'route', 'get'):
            try:
                ipaddress.ip_address(a[4])
                return True
            except ValueError:
                return False
        if (len(a) == 6 and a[:2] == ('ip', '-j') and a[2] in ('addr', 'address') and a[3:5] == ('show', 'dev')
                and (a[5] == 'eth0' or re.fullmatch(r'bnep[0-9]{1,4}', a[5]))):
            return True
        if (len(a) == 4 and a[:2] == ('iw', 'dev') and a[3] == 'info'
                and re.fullmatch(r'[A-Za-z0-9_-]{1,15}', a[2])):
            return True
        if (len(a) == 6 and a[:5] == ('nmcli', '-g', 'GENERAL.NM-MANAGED,GENERAL.STATE', 'device', 'show')
                and re.fullmatch(r'[A-Za-z0-9_-]{1,15}', a[5])):
            return True
        # Only read-only D-Bus properties and owner lookup; never auto-start.
        if a[:4] == ('busctl', '--system', '--auto-start=no', 'get-property'):
            return (len(a) == 8 and (
                a[4:] == ('fi.w1.wpa_supplicant1', '/fi/w1/wpa_supplicant1',
                           'fi.w1.wpa_supplicant1', 'Interfaces') or
                a[4] == 'fi.w1.wpa_supplicant1' and
                re.fullmatch(r'/fi/w1/wpa_supplicant1/Interfaces/[0-9]+', a[5]) and
                a[6:] == ('fi.w1.wpa_supplicant1.Interface', 'Ifname') or
                re.fullmatch(r':[0-9]+\.[0-9]+', a[4]) and
                re.fullmatch(r'/org/bluez/hci[0-9]+(?:/dev_(?:[0-9A-F]{2}_){5}[0-9A-F]{2})?', a[5]) and
                (a[6], a[7]) in {
                    ('org.bluez.Adapter1', 'Powered'), ('org.bluez.Adapter1', 'Pairable'),
                    ('org.bluez.Adapter1', 'Discoverable'), ('org.bluez.Device1', 'Paired'),
                    ('org.bluez.Device1', 'Trusted'), ('org.bluez.Device1', 'Bonded'),
                    ('org.bluez.Device1', 'Adapter'), ('org.bluez.Device1', 'Address'),
                    ('org.bluez.Network1', 'Connected'), ('org.bluez.Network1', 'Interface'),
                    ('org.bluez.Network1', 'UUID')}))
        return a == ('busctl', '--system', '--auto-start=no', 'call', 'org.freedesktop.DBus',
                     '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetNameOwner', 's', 'org.bluez')

    def __call__(self, args, *, stdin=None):
        if not self.allowed(args):
            raise Unavailable('command_denied')
        remaining = min(self.command_seconds, self.deadline - self.clock())
        if remaining <= 0:
            raise Unavailable('deadline')
        try:
            with subprocess.Popen(args, stdin=stdin if stdin is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                                  'LC_ALL': 'C'}, start_new_session=True) as p:
                try:
                    data = bytearray()
                    end = self.clock() + remaining
                    with selectors.DefaultSelector() as sel:
                        sel.register(p.stdout, selectors.EVENT_READ)
                        while True:
                            left = end - self.clock()
                            if left <= 0 or not sel.select(left):
                                raise Unavailable('command_timeout')
                            chunk = os.read(p.stdout.fileno(), 8192)
                            if not chunk:
                                break
                            data.extend(chunk)
                            if len(data) > 131072:
                                raise Unavailable('command_overflow')
                    p.wait(timeout=max(0.001, end-self.clock()))
                    if p.returncode:
                        raise Unavailable('command_failed')
                    return data.decode('utf-8', errors='strict')
                finally:
                    if p.poll() is None:
                        p.kill()
                        p.wait(timeout=2)
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            raise Unavailable('command_unavailable') from None


def read_small(path, limit=4096):
    with Path(path).open('rb') as f:
        value = f.read(limit + 1)
    if len(value) > limit:
        raise Unavailable('file_overflow')
    return value.decode('ascii').strip()


def inventory(sys_root):
    """Pin all wireless netdev generations and their physical ancestry privately."""
    result = {}
    for n, net in enumerate(Path(sys_root).iterdir()):
        if n >= 256:
            raise Unavailable('inventory_overflow')
        if not (net/'phy80211').exists():
            continue
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,15}', net.name):
            raise Unavailable('interface_invalid')
        st = net.stat()
        device = (net/'device').resolve(strict=True)
        buses = tuple((p/'subsystem').resolve().name for p in (device, *device.parents)
                      if (p/'subsystem').is_symlink())
        # SDIO attachment is affirmative; unknown transports never become onboard
        # merely because a driver name resembles the onboard driver.
        transport = 'usb' if 'usb' in buses else 'onboard' if 'sdio' in buses else 'unknown'
        result[net.name] = (st.st_dev, st.st_ino, int(read_small(net/'ifindex')),
                            (net/'phy80211').resolve(strict=True).name,
                            int(read_small(net/'type')), int(read_small(net/'flags'), 16),
                            str(device), device.stat().st_ino, transport)
    if len(result) > 32:
        raise Unavailable('radio_overflow')
    return result


def iw_interfaces(text):
    result, phy, current = {}, None, None
    for line in text.splitlines():
        m = re.fullmatch(r'\s*phy#([0-9]+)\s*', line)
        if m:
            phy, current = 'phy' + m[1], None
        m = re.fullmatch(r'\s*Interface ([A-Za-z0-9_-]{1,15})\s*', line)
        if m:
            if m[1] in result or phy is None:
                raise Unavailable('iw_identity_invalid')
            current = result[m[1]] = {'phy': phy}
        if current is not None:
            m = re.fullmatch(r'\s*(ifindex|type) ([A-Za-z0-9_-]+)\s*', line)
            if m:
                if m[1] in current:
                    raise Unavailable('iw_identity_invalid')
                current[m[1]] = m[2]
    return result


def collect(peer, *, run=None, sys_root=Path('/sys/class/net'), proc_root=Path('/proc'),
            clock=time.monotonic, verifier_factory=KernelUsbVerifier, ownership_checks=None, diagnostics=None):
    """One attempt. Root-only ownership measurement; no PID exemption or RF claim.

    Stamp START, recheck whole inventory, boot and Ethernet at end. Any collection
    uncertainty aborts the receipt. Ownership uncertainty stays false, never a
    claim of unmanaged ownership. A running Bettercap is deliberately not exempt.
    """
    ipaddress.ip_address(peer)
    run = run or BoundedReader(clock=clock)
    start = clock()
    boot_path = Path(proc_root)/'sys/kernel/random/boot_id'
    boot = read_small(boot_path)
    before = inventory(sys_root)
    def processes():
        result = {}
        for n, directory in enumerate(Path(proc_root).iterdir()):
            if not 0 <= clock() - start <= 3:
                raise Unavailable('observation_stale')
            if n >= 8192:
                raise Unavailable('process_inventory_overflow')
            if not directory.name.isdigit():
                continue
            # A process disappearing during the snapshot is uncertainty.
            name = read_small(directory/'comm')
            if name in {'wpa_supplicant', 'bettercap', 'hostapd', 'iwd', 'tcpdump',
                        'dumpcap', 'tshark', 'airodump-ng', 'hcxdumptool', 'kismet'}:
                result[directory.name] = (name,
                    read_small(directory/'stat', 16384).rsplit(')', 1)[1].split()[19],
                    read_small(directory/'cgroup', 16384))
        return result
    owners_before = processes()
    raw_phy = run(['iw', 'phy'])
    phy = parse_iw_phy(raw_phy)
    # Only Supported interface modes advertises capability. Combination limits
    # or a driver name cannot substitute for that explicit nl80211 attribute.
    for block in re.split(r'(?m)^Wiphy ', raw_phy)[1:]:
        ph = block.splitlines()[0].strip()
        modes = re.search(r'(?m)^\s*Supported interface modes:\s*\n((?:[ \t]+\*[^\n]*\n?)+)', block)
        if not modes or ph not in phy:
            raise Unavailable('capabilities_unknown')
        phy[ph]['monitor'] = bool(re.search(r'(?m)^\s*\* monitor\s*$', modes[1]))
    dev = iw_interfaces(run(['iw', 'dev']))
    if set(dev) != set(before):
        raise Unavailable('inventory_disagreement')

    def ethernet():
        routes = json.loads(run(['ip', '-j', 'route', 'get', peer]))
        addr = json.loads(run(['ip', '-j', 'addr', 'show', 'dev', 'eth0']))
        if (len(routes) != 1 or routes[0].get('dev') != 'eth0' or len(addr) != 1
                or addr[0].get('ifname') != 'eth0' or 'UP' not in addr[0].get('flags', [])
                or type(addr[0].get('ifindex')) is not int or not addr[0].get('addr_info')):
            raise Unavailable('ethernet_unverified')
        source = routes[0].get('prefsrc')
        if (routes[0].get('type', 'unicast') != 'unicast'
                or source not in [a.get('local') for a in addr[0]['addr_info']]):
            raise Unavailable('ethernet_unverified')
        return (addr[0]['ifindex'], source, sorted((a['family'], a['local'], a['prefixlen'])
                                         for a in addr[0]['addr_info']))

    eth = ethernet()
    def nm(name):
        try:
            raw = run(['nmcli', '-g', 'GENERAL.NM-MANAGED,GENERAL.STATE', 'device', 'show', name])
            found = re.fullmatch(r'(yes|no)\n([0-9]{1,3})(?: \([^\n]*\))?\s*', raw)
            if found and int(found[2]) <= 120:
                return found[1] == 'yes', int(found[2])
        except Unavailable:
            pass
        return None, None
    nm_before = {name: nm(name) for name in before}
    rows = []
    for name, identity in sorted(before.items()):
        _, _, index, ph, arphrd, flags, _, _, transport = identity
        info = dev[name]
        if (info.get('phy') != ph or info.get('ifindex') != str(index) or ph not in phy
                or not re.fullmatch(r'phy[0-9]+', ph) or index <= 0):
            raise Unavailable('radio_identity_disagreement')
        if os.geteuid() == 0 and transport == 'usb':
            try:
                verify = verifier_factory(peer, sys_root=Path(sys_root), proc_root=Path(proc_root))
                # Reuse the independently measured live kernel/NM/process checks,
                # but feed them this bounded read-only command transport.
                verify._command = lambda *args: run(list(args))
                verify(CaptureLease(name, ph, index), None)
                # Exclusion is useful evidence, but process names/cgroups do not
                # authenticate an exclusive capture owner. Never promote it.
                if ownership_checks is not None:
                    ownership_checks[name] = True
            except (LeaseLost, OSError, ValueError, KeyError, TypeError, Unavailable):
                if ownership_checks is not None:
                    ownership_checks[name] = False
        if diagnostics is not None:
            try:
                driver = (Path(sys_root)/name/'device/driver').resolve(strict=True).name
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', driver):
                    driver = None
            except OSError:
                driver = None
            diagnostics[name] = dict(nm_managed=nm_before[name][0], nm_state=nm_before[name][1],
                driver=driver, identity_stable=True, usb_ancestry=transport == 'usb',
                # These are conflict indicators, never an exhaustive owner proof.
                conflicting_processes=sum(v[0] in {'bettercap','hostapd','iwd','tcpdump',
                    'dumpcap','tshark','airodump-ng','hcxdumptool','kismet'} for v in owners_before.values()))
        rows.append(dict(iface=name, phy=ph, ifindex=index, monitor=phy[ph]['monitor'],
                         bands=sorted(phy[ph]['bands']), mode=info.get('type') if info.get('type') in
                         ('managed', 'monitor') else 'unknown',
                         arphrd=arphrd, decoded_frames=None, owner_verified=False,
                         owner='unknown', transport=transport))
    if before != inventory(sys_root):
        raise Unavailable('radio_inventory_changed')
    if boot != read_small(boot_path):
        raise Unavailable('boot_changed')
    if eth != ethernet():
        raise Unavailable('ethernet_changed')
    if dev != iw_interfaces(run(['iw', 'dev'])):
        raise Unavailable('iw_identity_changed')
    if owners_before != processes():
        raise Unavailable('process_ownership_changed')
    if nm_before != {name: nm(name) for name in before}:
        raise Unavailable('nm_changed')
    elapsed = clock() - start
    if not math.isfinite(elapsed) or not 0 <= elapsed <= 3:
        raise Unavailable('observation_stale')
    receipt = dict(boot_id=boot, sampled_monotonic=start, ethernet_verified=True, radios=rows)
    # An empty inventory is a useful diagnostic but cannot be a lease receipt.
    if rows:
        validate_receipt(receipt, boot)
    return receipt


def publish(receipt, destination, *, clock=time.monotonic):
    """Explicit future provisioning API, never called by read-only acceptance.

    Root-only atomic 0600 replacement in a root-owned non-writable directory.
    No symlink ancestor, no permissive ancestor except root-owned sticky /tmp.
    Never refresh an old sample or write an empty/qualified-by-metadata receipt.
    """
    path = Path(destination)
    age = clock() - receipt['sampled_monotonic']
    if (os.geteuid() != 0 or not path.is_absolute() or '..' in path.parts
            or not math.isfinite(age) or not 0 <= age <= 3
            or receipt.get('boot_id') != read_small('/proc/sys/kernel/random/boot_id')
            or not 1 <= len(receipt.get('radios', [])) <= 32
            or any(r.get('decoded_frames') is not None for r in receipt['radios'])):
        raise Unavailable('receipt_refused')
    for parent in (path.parent, *path.parent.parents):
        s = parent.lstat()
        if (not stat.S_ISDIR(s.st_mode) or s.st_uid != 0
                or s.st_mode & 0o022 and not (s.st_mode & stat.S_ISVTX and parent != path.parent)):
            raise Unavailable('directory_untrusted')
    try:
        validate_receipt(receipt, read_small('/proc/sys/kernel/random/boot_id'))
    except (ValueError, KeyError, TypeError):
        raise Unavailable('receipt_schema_invalid') from None
    if path.exists() or path.is_symlink():
        previous = path.lstat()
        if (not stat.S_ISREG(previous.st_mode) or previous.st_uid != 0 or previous.st_mode & 0o022):
            raise Unavailable('destination_untrusted')
    raw = json.dumps(receipt, allow_nan=False).encode()
    if len(raw) > 65536:
        raise Unavailable('receipt_overflow')
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = None
    try:
        opened = os.fstat(directory)
        current = path.parent.stat()
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise Unavailable('directory_changed')
        # Parent cannot be renamed by an unprivileged writer after validation.
        fd, temporary = tempfile.mkstemp(prefix='.usb-evidence-', dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            if not 0 <= clock() - receipt['sampled_monotonic'] <= 3:
                raise Unavailable('observation_stale')
            os.replace(Path(temporary).name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            temporary = None
            try:
                os.fsync(directory)
            except OSError:
                # Rename may already be visible: do not claim old state survived
                # and do not perform a blind rollback over another writer.
                raise Unavailable('receipt_publication_uncertain') from None
        finally:
            if temporary is not None:
                os.unlink(temporary)
    finally:
        os.close(directory)
