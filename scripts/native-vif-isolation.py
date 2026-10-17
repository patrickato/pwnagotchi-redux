#!/usr/bin/env python3
"""One approved per-device NM/native-VIF trial; default is read-only preflight.

Stream over previously verified Ethernet SSH using python3 -B. No installation,
configuration edits, packet files, driver reloads or global manager operations.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import struct
import subprocess
import time

REVISION = '4f907ff93ce0c6bc0b2c9d4f68025ba7dd1f9a84'
KERNEL = '6.12.109+rpt-rpi-v8'
DRIVER_HASH = '0ebeaf3767e2210304357fb80bf3386715c4ecddcfd2aeccadde83714078a3ba'


class Stop(RuntimeError):
    pass


@contextmanager
def single_trial_lock():
    """Kernel-only singleton guard; no lock file, daemon or persistent state."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as lock:
        try:
            lock.bind('\0redux-native-vif-trial')
        except OSError:
            raise Stop('another trial may be active; do not retry') from None
        yield


def decode_header(packet, arphrd):
    """Classify complete 802.11 MAC headers only; never return addresses/payload."""
    if arphrd == 803:
        if len(packet) < 8 or packet[0:2] != b'\0\0':
            return 'invalid'
        size = struct.unpack_from('<H', packet, 2)[0]
        if size < 8 or size > len(packet):
            return 'invalid'
        packet = packet[size:]
    elif arphrd != 801:
        return 'invalid'
    if len(packet) < 2:
        return 'invalid'
    fc = int.from_bytes(packet[:2], 'little')
    if fc & 3:
        return 'invalid'
    kind, subtype = (fc >> 2) & 3, (fc >> 4) & 15
    if kind == 0:
        needed = 36 if subtype in (5, 8) else 24
        label = 'management'
    elif kind == 1:
        needed = 10 if subtype in (12, 13) else 16
        label = 'control'
    elif kind == 2:
        needed = 24 + (6 if fc & 0x300 == 0x300 else 0)
        if subtype & 8:
            needed += 2 + (4 if fc & 0x8000 else 0)
        label = 'data'
    else:
        return 'invalid'
    return label if len(packet) >= needed else 'invalid'


def primary_is(s, mode, up):
    """Kernel mode/UP is a separate assertion from stable-device safety."""
    return s['primary_mode'] == mode and s['primary_up'] is up


def isolated_primary(s):
    """Only the *measured* NM side effect is supported in this candidate."""
    return (s['safe'] is True and s['managed'] is False
            and s['nm_state'] == 10 and s['supplicant'] == []
            and primary_is(s, 'managed', False)
            and s['primary_type'] == 1
            and s['live'] == 'inactive' and s['capture_process'] is False)


def vif_gate_snapshot(s, name, phy, *, stage, expected_index=None):
    """Allowlisted read-only gate evidence captured BEFORE abort or cleanup.

    Never output frames, addresses, connection profiles, raw subprocess text,
    or unbounded external strings. The kernel/manager observations are already
    collected by observe(); this function performs no extra hardware action.
    """
    index = s.get('temp_index')
    observed = {key: s.get(key) for key in (
        'safe', 'managed', 'nm_state', 'primary_mode', 'primary_up',
        'primary_type', 'live', 'capture_process', 'firmware',
        'temp_index', 'temp_type', 'temp_monitor', 'temp_nm_managed', 'temp_up',
    )}
    # Never serialize arbitrary interface names/paths from the environment.
    observed['temp_present'] = name in s.get('interfaces', ())
    observed['temp_phy_matches'] = s.get('temp_phy') == phy
    checks = {
        'primary_isolated': isolated_primary(s),
        'temporary_interface_present': observed['temp_present'],
        'temporary_phy_matches': observed['temp_phy_matches'],
        'temporary_mode_monitor': s.get('temp_monitor') is True,
        'temporary_ifindex_measured': type(index) is int and index > 0,
        'temporary_nm_unmanaged': s.get('temp_nm_managed') is False,
        'native_80211_link_type': s.get('temp_type') in (801, 803),
        'firmware_mode_expected': s.get('firmware') == (0 if stage == 'created' else 2),
    }
    if stage == 'activated':
        checks['temporary_ifindex_unchanged'] = (
            type(index) is int and index == expected_index)
    return {'stage': stage, 'observed': observed,
            'failed_checks': sorted(k for k, passed in checks.items() if not passed)}


def baseline_ok(s):
    return (s['safe'] is True and s['interfaces'] == ['wlan0']
            and s['managed'] is True and s['nm_state'] == 20
            and s['supplicant'] == [] and s['live'] == 'active'
            and s['firmware'] == 0 and s['primary_type'] == 1
            and primary_is(s, 'monitor', True)
            and s['runtime_fresh'] and s['runtime_state'] == 'running' and not s['runtime_error'])


def run_trial(ops, name, *, approved=False, down_only=False):
    """Bounded state machine; backend injection tests the real rollback logic."""
    if not approved:
        raise Stop('execution requires explicit operator approval')
    if not re.fullmatch(r'rxv[0-9a-f]{8}', name):
        raise Stop('invalid temporary VIF name')
    if down_only and getattr(ops, 'down_only', False) is not True:
        raise Stop('DOWN-only backend required')
    before = ops.observe(name)
    if down_only and before['phy'] != 'phy0':
        raise Stop('DOWN-only profile requires verified phy0')
    if not baseline_ok(before):
        raise Stop('baseline prerequisites not satisfied')
    log = {'before': ops.public(before), 'test': 'not_started', 'cleanup': [],
           'temporary_vif': name, 'profile': 'down_only' if down_only else 'native_monitor'}
    stopped = changed = created_attempt = False
    owned_index = None
    ops.event('baseline', baseline=ops.public(before), temporary_vif=name)
    try:
        stopped = True  # record intent BEFORE a possibly partial operation
        ops.change(['systemctl', 'stop', 'redux-live.service'])
        s = ops.observe(name)
        if (not s['safe'] or not primary_is(s, 'monitor', True)
                or s['live'] != 'inactive' or s['capture_process']
                or s['managed'] is not True or s['nm_state'] != 20):
            raise Stop('capture owner did not release radio cleanly')
        changed = True
        ops.change(['nmcli', '--wait', '5', 'device', 'set', 'wlan0', 'managed', 'no'])
        s = ops.observe(name)
        log['isolation'] = ops.public(s)
        if (not isolated_primary(s) or s['interfaces'] != before['interfaces']
                or s['firmware'] != 0 or s['capture_process']):
            raise Stop('expected managed/DOWN isolation not confirmed')
        created_attempt = True
        ops.change(['iw', 'phy', before['phy'], 'interface', 'add', name, 'type', 'monitor'])
        s = ops.observe(name)
        log['post_create'] = vif_gate_snapshot(s, name, before['phy'], stage='created')
        owned_index = ops.temp_index(s, name, before['phy'])
        ops.event('post_create', gate=log['post_create'])
        if down_only:
            # Unknown NM discovery is evidence to observe, never permission
            # to activate. All identity/link/firmware gates still apply.
            failures = set(log['post_create']['failed_checks']) - {'temporary_nm_unmanaged'}
            if failures or s.get('temp_up') is not False or s['temp_nm_managed'] is True:
                raise Stop('DOWN-only interface prerequisites failed')
            log['test'] = {'down_only': True, 'arphrd': s['temp_type'],
                           'firmware': s['firmware'],
                           'ownership': ops.observe_down(name, owned_index, before['phy'])}
        else:
            if log['post_create']['failed_checks']:
                raise Stop('temporary interface isolation not confirmed')
            ops.event('created', temporary_vif=name, ifindex=owned_index, phy=before['phy'])
            ops.change(['ip', 'link', 'set', 'dev', name, 'up'])
            s = ops.observe(name)
            log['post_activate'] = vif_gate_snapshot(
                s, name, before['phy'], stage='activated', expected_index=owned_index)
            ops.event('post_activate', gate=log['post_activate'])
            if log['post_activate']['failed_checks']:
                raise Stop('native monitor activation did not pass')
            log['test'] = {'arphrd': s['temp_type'], 'firmware': s['firmware'],
                           'reception': ops.sample(name, s['temp_type'], seconds=60)}
    except BaseException as exc:
        # Never publish command stderr, frames, connection names or arbitrary exceptions.
        log['test'] = {'stopped': str(exc) if isinstance(exc, Stop) else type(exc).__name__}
    finally:
        ops.begin_cleanup()
        try:
            s = ops.observe(name)
            if created_attempt and name in s['interfaces']:
                if down_only and owned_index is None:
                    raise Stop('DOWN-only created identity was not pinned; refuse deletion')
                current_index = ops.temp_index(s, name, before['phy'])
                if current_index is None or (owned_index is not None and current_index != owned_index):
                    raise Stop('temporary interface identity changed; refuse deletion')
                for args in (['ip', 'link', 'set', 'dev', name, 'down'],
                             ['iw', 'dev', name, 'del']):
                    try:
                        ops.change(args)
                    except Stop:
                        log['cleanup'].append('temporary interface cleanup command failed')
                s = ops.observe(name)
            log['cleanup_observed'] = ops.public(s)
            if (not s['safe'] or s['interfaces'] != before['interfaces']
                    or s['firmware'] != before['firmware']
                    or s['primary_type'] != before['primary_type']
                    or s['primary_ifindex'] != before['primary_ifindex']
                    or s['supplicant'] != []):
                raise Stop('radio baseline not restored; leave manager/service unchanged')
            if changed and (s['live'] != 'inactive' or s['capture_process']):
                raise Stop('capture owner returned; leave manager/service unchanged')
            if changed and s['managed'] is False:
                if s['nm_state'] != 10:
                    raise Stop('unmanaged ownership state uncertain; leave primary unchanged')
                # NM's runtime release *actually* changed wlan0 to managed
                # mode and DOWN on the field Pi. Restore the kernel baseline
                # explicitly, exactly once per necessary step, BEFORE
                # returning control to NM and BEFORE restarting Bettercap.
                if primary_is(s, 'managed', False):
                    ops.change(['iw', 'dev', 'wlan0', 'set', 'type', 'monitor'])
                    s = ops.observe(name)
                    if (not s['safe'] or s['interfaces'] != before['interfaces']
                            or s['managed'] is not False or s['nm_state'] != 10
                            or s['live'] != 'inactive' or s['capture_process']
                            or s['primary_type'] != before['primary_type']
                            or s['firmware'] != before['firmware']
                            or s['primary_mode'] != 'monitor'
                            or s['primary_ifindex'] != before['primary_ifindex']):
                        raise Stop('original wlan0 monitor mode was not restored')
                if s['primary_mode'] == 'monitor' and s['primary_up'] is False:
                    ops.change(['ip', 'link', 'set', 'dev', 'wlan0', 'up'])
                    s = ops.observe(name)
                if (not s['safe'] or s['interfaces'] != before['interfaces']
                        or not primary_is(s, 'monitor', True)
                        or s['managed'] is not False or s['nm_state'] != 10
                        or s['live'] != 'inactive' or s['capture_process']
                        or s['primary_type'] != before['primary_type']
                        or s['firmware'] != before['firmware']
                        or s['primary_ifindex'] != before['primary_ifindex']):
                    raise Stop('original wlan0 link state not restored')
                ops.change(['nmcli', '--wait', '5', 'device', 'set', 'wlan0', 'managed', 'yes'])
            s = ops.restored_ownership(name, before)
            if (not s['safe'] or s['interfaces'] != before['interfaces']
                    or s['firmware'] != before['firmware']
                    or s['primary_type'] != before['primary_type']
                    or s['primary_ifindex'] != before['primary_ifindex']
                    or s['managed'] != before['managed'] or s['nm_state'] != before['nm_state']
                    or not primary_is(s, before['primary_mode'], before['primary_up'])
                    or s['supplicant'] != []):
                raise Stop('manager ownership baseline not restored; leave live stopped')
            if stopped and s['live'] == 'inactive':
                ops.change(['systemctl', 'start', 'redux-live.service'])
            log['after'] = ops.verify_live(name, before)
            log['restored'] = True
        except BaseException as exc:
            log['restored'] = False
            log['cleanup'].append(str(exc) if isinstance(exc, Stop) else type(exc).__name__)
    return log


class PiOps:
    def __init__(self, connection):
        fields = connection.split()
        if len(fields) != 4:
            raise Stop('SSH connection metadata required')
        self.peer = str(ipaddress.ip_address(fields[0]))
        self.deadline = time.monotonic() + 210
        self.storage = self.ethernet = self.primary = None
        self.cleaning = False

    def command(self, args, *, required=True):
        if time.monotonic() >= self.deadline:
            raise Stop('phase deadline reached')
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=min(getattr(self, "command_limit", 6), max(0.001, self.deadline-time.monotonic())), check=False,
                               env={**os.environ, 'LC_ALL': 'C'})
        except (OSError, subprocess.TimeoutExpired):
            raise Stop('bounded command unavailable or timed out') from None
        if p.returncode and required:
            raise Stop('command failed: ' + args[0])
        return p.returncode, p.stdout[:131072]

    def event(self, phase, **values):
        try:
            print(json.dumps({'phase': phase, **values}, sort_keys=True), flush=True)
        except BrokenPipeError:
            if not self.cleaning:
                raise Stop('output connection lost') from None

    def change(self, args):
        self.command(args)

    def begin_cleanup(self):
        self.cleaning = True
        signal.alarm(0)
        self.deadline = time.monotonic() + 90

    def nm(self, iface, *, absent_ok=False):
        _, devices = self.command(['nmcli', '-g', 'DEVICE', 'device', 'status'])
        if iface not in devices.splitlines():
            if absent_ok:
                # Absence from NM discovery is NOT evidence of a device
                # being explicitly unmanaged. Wait for an observed 'no'
                # rather than activating a VIF under uncertain ownership.
                return None, None
            raise Stop('original NM device missing')
        _, raw = self.command(['nmcli', '-g', 'GENERAL.NM-MANAGED,GENERAL.STATE', 'device', 'show', iface])
        lines = raw.splitlines()
        if len(lines) != 2 or lines[0] not in ('yes', 'no'):
            raise Stop('invalid NM measurement')
        state = re.match(r'^(\d+)\b', lines[1])
        if not state:
            raise Stop('invalid NM state')
        return lines[0] == 'yes', int(state.group(1))

    def supplicant(self):
        _, raw = self.command(['busctl', '--system', '--auto-start=no', 'get-property', 'fi.w1.wpa_supplicant1',
                              '/fi/w1/wpa_supplicant1', 'fi.w1.wpa_supplicant1', 'Interfaces'])
        if not re.fullmatch(r'ao 0\s*', raw):
            # Any binding, including an unexpected other radio, stops this one-radio trial.
            raise Stop('supplicant is not demonstrably unbound')
        if Path('/run/wpa_supplicant/wlan0').exists():
            raise Stop('supplicant control socket appeared')
        return []

    def observe(self, name):
        for unit in ('NetworkManager.service', 'wpa_supplicant.service'):
            self.command(['systemctl', 'is-active', unit])
        _, route = self.command(['ip', '-j', 'route', 'get', self.peer])
        routes = json.loads(route)
        route_ok = len(routes) == 1 and routes[0].get('dev') == 'eth0'
        _, eth = self.command(['ip', '-j', 'address', 'show', 'dev', 'eth0'])
        eth = json.loads(eth)[0]
        identity = (eth['ifindex'], tuple(sorted(a['local'] for a in eth['addr_info'])))
        self.ethernet = self.ethernet or identity
        _, mount = self.command(['findmnt', '-rn', '-T', '/captures', '-o', 'TARGET,FSTYPE,OPTIONS'])
        bits = mount.split()
        storage = (os.stat('/captures').st_dev, os.stat('/captures').st_ino)
        self.storage = self.storage or storage
        mount_ok = len(bits) == 3 and bits[:2] == ['/captures', 'ext4'] and 'rw' in bits[2].split(',')
        _, reg = self.command(['iw', 'reg', 'get'])
        _, rf = self.command(['rfkill', 'list'])
        _, iw = self.command(['iw', 'dev'])
        interfaces = re.findall(r'(?m)^\s*Interface ([A-Za-z0-9_.-]{1,15})\s*$', iw)
        _, primary = self.command(['iw', 'dev', 'wlan0', 'info'])
        phy = re.search(r'(?m)^\s*wiphy (\d+)\s*$', primary)
        mode = re.search(r'(?m)^\s*type (\S+)\s*$', primary)
        managed, state = self.nm('wlan0')
        _, fw = self.command(['nexutil', '-I', name if name in interfaces else 'wlan0', '-m'])
        firmware = re.search(r'(?m)^monitor:\s*(\d+)\s*$', fw)
        _, module = self.command(['modinfo', '-n', 'brcmfmac'])
        module = module.strip()
        module_ok = bool(re.fullmatch(r'/(usr/)?lib/modules/' + re.escape(KERNEL) + r'/updates/brcmfmac\.ko', module))
        module_ok = module_ok and hashlib.sha256(Path(module).read_bytes()).hexdigest() == DRIVER_HASH
        _, srcversion = self.command(['modinfo', '-F', 'srcversion', 'brcmfmac'])
        module_ok = module_ok and Path('/sys/module/brcmfmac/srcversion').read_text().strip() == srcversion.strip()
        _, processes = self.command(['ps', '-eo', 'comm=,cgroup='])
        capture = False
        conflict = False
        supplicant_count = manager_count = 0
        for line in processes.splitlines():
            words = line.split(None, 1)
            if not words:
                continue
            if words[0] == 'wpa_supplicant':
                supplicant_count += 1
                if len(words) != 2 or '/wpa_supplicant.service' not in words[1]:
                    conflict = True
            if words[0] == 'NetworkManager':
                manager_count += 1
                if len(words) != 2 or '/NetworkManager.service' not in words[1]:
                    conflict = True
            if words[0] == 'bettercap':
                capture = True
                if len(words) != 2 or '/redux-live.service' not in words[1]:
                    conflict = True
            if words[0] in {'hostapd', 'iwd', 'tcpdump', 'dumpcap', 'tshark', 'airodump-ng', 'hcxdumptool', 'kismet'}:
                conflict = True
        _, connected = self.command(['iw', 'dev', 'wlan0', 'link'])
        _, kernel_log = self.command(['journalctl', '-k', '-b', '-n', '200', '--no-pager', '-o', 'cat'])
        kernel_error = bool(re.search(r'Oops:|Kernel panic|Device or resource busy|\(-16\)|brcmf.*(?:firmware.*(?:crash|trap)|error)', kernel_log, re.I))
        try:
            runtime = json.loads(Path('/captures/live/live.json').read_text())
            stamp = runtime.get('updated_utc')
            runtime_fresh = type(stamp) in (int, float) and 0 <= time.time()-stamp <= 12
        except (OSError, ValueError):
            runtime, runtime_fresh = {}, False
        _, live = self.command(['systemctl', 'is-active', 'redux-live.service'], required=False)
        s = {'interfaces': sorted(interfaces), 'managed': managed, 'nm_state': state,
             'supplicant': self.supplicant(), 'live': live.strip(), 'capture_process': capture,
             'firmware': int(firmware.group(1)) if firmware else None,
             'primary_type': int(Path('/sys/class/net/wlan0/type').read_text()),
             'primary_ifindex': int(Path('/sys/class/net/wlan0/ifindex').read_text()),
             'primary_up': bool(int(Path('/sys/class/net/wlan0/flags').read_text().strip(), 16) & 1),
             'primary_mode': mode.group(1) if mode and mode.group(1) in {'monitor', 'managed'} else None,
             'runtime_fresh': runtime_fresh, 'runtime_state': runtime.get('state'),
             'runtime_error': bool(runtime.get('last_error') or runtime.get('failure_streak')),
             'health': runtime.get('health') if runtime.get('health') in {'ok','attention','degraded','action','unknown'} else None,
             'phy': 'phy' + phy.group(1) if phy else None,
             'temp_index': None, 'temp_type': None, 'temp_phy': None, 'temp_monitor': False,
             'temp_nm_managed': None, 'temp_up': None}
        primary_identity = (s['primary_ifindex'], s['phy'])
        self.primary = self.primary or primary_identity
        name_collision = Path('/sys/class/net', name).exists() and name not in interfaces
        s['safe'] = bool(not name_collision and primary_identity == self.primary and route_ok and identity == self.ethernet and mount_ok and storage == self.storage
                         and os.statvfs('/captures').f_bavail * os.statvfs('/captures').f_frsize > 64*1024*1024
                         and os.uname().release == KERNEL and os.uname().machine == 'aarch64'
                         and Path('/usr/share/redux/redux-revision').read_text().strip() == REVISION
                         and Path('/proc/device-tree/model').read_bytes().startswith(b'Raspberry Pi 4 Model B')
                         and module_ok and not kernel_error and re.search(r'global\s+country US:', reg)
                         and Path('/etc/redux/regulatory-country').read_text().strip() == 'US'
                         and not re.search(r'(Soft|Hard) blocked:\s*yes', rf)
                         and not re.search(r'^\s*Connected to', connected, re.M)
                         and s['primary_mode'] is not None and phy and not conflict
                         and supplicant_count == 1 and manager_count == 1
                         and set(interfaces) <= {'wlan0', name})
        if name in interfaces:
            _, info = self.command(['iw', 'dev', name, 'info'])
            p = re.search(r'(?m)^\s*wiphy (\d+)\s*$', info)
            s.update(temp_index=int(Path('/sys/class/net', name, 'ifindex').read_text()),
                     temp_up=bool(int(Path('/sys/class/net', name, 'flags').read_text().strip(), 16) & 1),
                     temp_type=int(Path('/sys/class/net', name, 'type').read_text()),
                     temp_phy='phy' + p.group(1) if p else None,
                     temp_monitor=bool(re.search(r'(?m)^\s*type monitor\s*$', info)),
                     temp_nm_managed=self.nm(name, absent_ok=True)[0])
        return s

    @staticmethod
    def temp_index(s, name, phy):
        return s['temp_index'] if (name in s['interfaces'] and s['temp_phy'] == phy and s['temp_monitor']) else None

    @staticmethod
    def public(s):
        return {k: s[k] for k in ('safe', 'interfaces', 'managed', 'nm_state', 'supplicant',
                                  'live', 'capture_process', 'firmware', 'primary_type', 'phy',
                                  'primary_up', 'primary_mode', 'primary_ifindex', 'runtime_fresh', 'health')}

    def restored_ownership(self, name, before):
        for _ in range(5):
            s = self.observe(name)
            if s['managed'] == before['managed'] and s['nm_state'] == before['nm_state']:
                return s
            time.sleep(1)
        return s

    def verify_live(self, name, before):
        started = time.time()
        for _ in range(30):
            s = self.observe(name)
            try:
                d = json.loads(Path('/captures/live/live.json').read_text())
                fresh = started - 1 <= d.get('updated_utc', 0) <= time.time() and time.time()-d['updated_utc'] <= 12
            except (OSError, ValueError, TypeError):
                fresh = False
            if (s['safe'] and s['interfaces'] == before['interfaces'] and s['managed'] == before['managed']
                    and s['nm_state'] == before['nm_state'] and s['live'] == before['live']
                    and s['firmware'] == before['firmware'] and s['primary_type'] == before['primary_type']
                    and s['primary_up'] == before['primary_up'] and s['primary_mode'] == before['primary_mode']
                    and s['primary_ifindex'] == before['primary_ifindex']
                    and s['health'] == before['health'] and s['capture_process']
                    and fresh and d.get('state') == 'running' and not d.get('failure_streak') and not d.get('last_error')):
                return self.public(s)
            time.sleep(1)
        raise Stop('live baseline not verified within bounded observation')

    def sample(self, name, arphrd, *, seconds):
        counts = dict(management=0, control=0, data=0, invalid=0)
        initial = self.observe(name)
        index = initial['temp_index']
        next_check = 0.0
        end = min(time.monotonic()+min(seconds, 60), self.deadline)
        old_deadline = self.deadline
        self.deadline = end
        with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3)) as sock:
            sock.bind((name, 0))
            sock.setblocking(False)
            while time.monotonic() < end and sum(counts.values()) < 10000:
                if time.monotonic() >= next_check:
                    state = self.observe(name)
                    if (not isolated_primary(state)
                            or state['temp_nm_managed'] is not False or state['temp_type'] != arphrd
                            or state['firmware'] != 2 or state['temp_index'] != index):
                        raise Stop('isolation changed during receive-only sample')
                    next_check = time.monotonic() + 5
                if time.monotonic() >= end:
                    break
                if not select.select([sock], [], [], min(1, max(0, end-time.monotonic())))[0]:
                    continue
                if time.monotonic() >= end:
                    break
                packet, address = sock.recvfrom(65535)
                if address[2] != 4:  # Never count locally outgoing packets as reception.
                    counts[decode_header(packet, arphrd)] += 1
        self.deadline = old_deadline
        return {'header_counts': counts, 'decoded_payloads': False, 'persistent_capture': False}


class DownOnlyPiOps(PiOps):
    """Capability-limited profile: no temporary UP, packet sockets or repeats."""
    down_only = True

    def __init__(self, connection):
        super().__init__(connection)
        self.attempted = set()

    def change(self, args):
        name = 'rxv5e16a901'
        setup = [
            ['systemctl', 'stop', 'redux-live.service'],
            ['nmcli', '--wait', '5', 'device', 'set', 'wlan0', 'managed', 'no'],
            ['iw', 'phy', 'phy0', 'interface', 'add', name, 'type', 'monitor'],
        ]
        cleanup = [
            ['ip', 'link', 'set', 'dev', name, 'down'], ['iw', 'dev', name, 'del'],
            ['iw', 'dev', 'wlan0', 'set', 'type', 'monitor'],
            ['ip', 'link', 'set', 'dev', 'wlan0', 'up'],
            ['nmcli', '--wait', '5', 'device', 'set', 'wlan0', 'managed', 'yes'],
            ['systemctl', 'start', 'redux-live.service'],
        ]
        allowed = cleanup if self.cleaning else setup
        key = tuple(args)
        if args not in allowed or key in self.attempted:
            raise Stop('DOWN-only command outside one-attempt profile')
        self.attempted.add(key)  # intent before partial side effects
        PiOps.command(self, args)

    def command(self, args, *, required=True):
        # Direct commands are read-only. Mutations can reach PiOps.command
        # only through change()'s exact phase/one-attempt allowlist above.
        fixed = [
            ['ip', '-j', 'route', 'get', self.peer],
            ['ip', '-j', 'address', 'show', 'dev', 'eth0'],
            ['findmnt', '-rn', '-T', '/captures', '-o', 'TARGET,FSTYPE,OPTIONS'],
            ['iw', 'reg', 'get'], ['rfkill', 'list'], ['iw', 'dev'],
            ['modinfo', '-n', 'brcmfmac'], ['modinfo', '-F', 'srcversion', 'brcmfmac'],
            ['ps', '-eo', 'comm=,cgroup='],
            ['journalctl', '-k', '-b', '-n', '200', '--no-pager', '-o', 'cat'],
            ['nmcli', '-g', 'DEVICE', 'device', 'status'],
            ['udevadm', 'info', '--query=property', '--path=/sys/class/net/rxv5e16a901'],
        ]
        for unit in ('NetworkManager.service', 'wpa_supplicant.service', 'redux-live.service'):
            fixed.append(['systemctl', 'is-active', unit])
        for iface in ('wlan0', 'rxv5e16a901'):
            for query in ('info', 'link'):
                fixed.append(['iw', 'dev', iface, query])
            fixed.append(['nexutil', '-I', iface, '-m'])
            fixed.append(['nmcli', '-g', 'GENERAL.NM-MANAGED,GENERAL.STATE', 'device', 'show', iface])
        bus = ['busctl', '--system', '--auto-start=no']
        fixed.append(bus + ['get-property', 'fi.w1.wpa_supplicant1',
            '/fi/w1/wpa_supplicant1', 'fi.w1.wpa_supplicant1', 'Interfaces'])
        for method in ('GetDevices', 'GetAllDevices'):
            fixed.append(bus + ['call', 'org.freedesktop.NetworkManager',
                '/org/freedesktop/NetworkManager', 'org.freedesktop.NetworkManager', method])
        device_property = (len(args) == 8 and args[:5] == bus + ['get-property', 'org.freedesktop.NetworkManager']
            and re.fullmatch(r'/org/freedesktop/NetworkManager/Devices/[0-9]+', args[5])
            and args[6] == 'org.freedesktop.NetworkManager.Device' and args[7] in ('Interface','Managed','State'))
        if args not in fixed and not device_property:
            raise Stop('command forbidden in DOWN-only read profile')
        return super().command(args, required=required)

    def sample(self, *args, **kwargs):
        raise Stop('packet sampling forbidden in DOWN-only profile')

    def observe_down(self, name, index, phy):
        if name != 'rxv5e16a901' or type(index) is not int or index <= 0:
            raise Stop('DOWN-only identity unavailable')
        original_deadline = self.deadline
        self.deadline = min(self.deadline, time.monotonic() + 10)
        self.command_limit = 2
        snapshots = []
        try:
            for _ in range(2):
                state = self.observe(name)
                if (not isolated_primary(state) or self.temp_index(state, name, phy) != index
                        or state['temp_up'] is not False or state['firmware'] != 0
                        or state['temp_type'] not in (801, 803)
                        or state['temp_nm_managed'] is True):
                    raise Stop('DOWN-only identity or safety changed')
                inventories = {}
                for method in ('GetDevices', 'GetAllDevices'):
                    _, raw = self.command(['busctl', '--system', '--auto-start=no', 'call',
                        'org.freedesktop.NetworkManager', '/org/freedesktop/NetworkManager',
                        'org.freedesktop.NetworkManager', method])
                    # Validate complete busctl ao output before using object paths.
                    match = re.fullmatch(r'ao ([0-9]+)((?: \"/org/freedesktop/NetworkManager/Devices/[0-9]+\")*)\s*', raw)
                    if not match:
                        raise Stop('invalid bounded D-Bus inventory')
                    paths = re.findall(r'\"([^\"]+)\"', match[2])
                    if len(paths) != int(match[1]) or len(paths) > 64:
                        raise Stop('D-Bus inventory exceeds bound')
                    found = []
                    for path in paths:
                        base = ['busctl', '--system', '--auto-start=no', 'get-property',
                                'org.freedesktop.NetworkManager', path, 'org.freedesktop.NetworkManager.Device']
                        _, raw_iface = self.command(base + ['Interface'])
                        if not raw_iface.startswith('s '):
                            raise Stop('invalid D-Bus interface measurement')
                        if json.loads(raw_iface[2:]) != name:
                            continue
                        _, managed = self.command(base + ['Managed'])
                        _, nm_state = self.command(base + ['State'])
                        if managed.strip() not in ('b true', 'b false') or not re.fullmatch(r'u [0-9]+\s*', nm_state):
                            raise Stop('invalid D-Bus ownership measurement')
                        found.append({'managed': managed.strip() == 'b true', 'state': int(nm_state[2:])})
                    inventories[method] = {'device_count': len(paths), 'matches': found}
                _, properties = self.command(['udevadm', 'info', '--query=property',
                                                '--path=/sys/class/net/' + name])
                # Expose only fixed classifications, never arbitrary udev values.
                vals = {}
                for key in ('NM_UNMANAGED', 'ID_NET_MANAGED_BY'):
                    entries = [line.split('=', 1)[1] for line in properties.splitlines() if line.startswith(key+'=')]
                    value = entries[0].lower() if len(entries) == 1 else None
                    vals[key] = ('absent' if not entries else 'true' if key == 'NM_UNMANAGED' and value in ('1','true')
                                 else 'false' if key == 'NM_UNMANAGED' and value in ('0','false')
                                 else 'present_uninterpreted')
                item = {'inventories': inventories, 'udev': vals, 'ifindex': index,
                        'arphrd': state['temp_type'], 'firmware': 0, 'up': False}
                snapshots.append(item)
                self.event('down_only_evidence', evidence=item)
                # Any positive manager claim stops observation, never activates.
                if any(m['managed'] for inv in inventories.values() for m in inv['matches']):
                    raise Stop('NM claimed DOWN-only temporary interface')
                time.sleep(min(1, max(0, self.deadline-time.monotonic())))
            final = self.observe(name)
            if (not isolated_primary(final) or self.temp_index(final, name, phy) != index
                    or final['temp_up'] is not False or final['firmware'] != 0):
                raise Stop('DOWN-only final identity changed')
            return snapshots
        finally:
            self.deadline = original_deadline
            self.command_limit = 6


def trial_exit_code(result, *, execution=False):
    """Classify actual experiment outcome, never infer success from cleanup alone.

    0: read-only preflight, or completed trial with verified restoration;
    2: experiment stopped with verified restoration, or preflight declined;
    3: restoration unverified; operator intervention may be needed.
    A successful experiment may still receive zero RF frames; this is NOT
    native capture acceptance.
    """
    if not execution:
        return 0 if result.get('prerequisites_satisfied') is True else 2
    if result.get('restored') is not True:
        return 3
    test = result.get('test')
    if not isinstance(test, dict) or 'stopped' in test:
        return 2
    return 0 if ('arphrd' in test and ('reception' in test or (test.get('down_only') is True and 'ownership' in test))) else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-approved', action='store_true')
    parser.add_argument('--down-only', action='store_true', help='bounded DOWN-only ownership observation; no RF activation')
    parser.add_argument('--vif', default='rxv5e16a901')
    parser.add_argument('ssh_connection')
    args = parser.parse_args()
    if not re.fullmatch(r'rxv[0-9a-f]{8}', args.vif):
        parser.error('invalid temporary VIF name')
    if os.geteuid() != 0:
        parser.error('root required for read-only driver/ownership inspection')
    if args.down_only and args.vif != 'rxv5e16a901':
        parser.error('DOWN-only profile uses only rxv5e16a901')
    ops = (DownOnlyPiOps if args.down_only else PiOps)(args.ssh_connection)
    def stop_signal(_signum, _frame):
        raise Stop('session signal or overall deadline')
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGALRM):
        signal.signal(sig, stop_signal)
    signal.alarm(210)
    if args.execute_approved:
        with single_trial_lock():
            result = run_trial(ops, args.vif, approved=True, down_only=args.down_only)
    else:
        s = ops.observe(args.vif)
        result = {'read_only': True, 'profile': 'down_only' if args.down_only else 'native_monitor', 'prerequisites_satisfied': baseline_ok(s), 'baseline': ops.public(s)}
    print(json.dumps(result, sort_keys=True, indent=2))
    return trial_exit_code(result, execution=args.execute_approved)


if __name__ == '__main__':
    raise SystemExit(main())
