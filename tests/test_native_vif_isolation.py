"""Synthetic one-radio ownership/rollback tests; never invoke hardware commands."""
import copy
import importlib.util
from pathlib import Path
import struct

import pytest

path = Path(__file__).resolve().parents[1] / 'scripts/native-vif-isolation.py'
spec = importlib.util.spec_from_file_location('native_vif_isolation', path)
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)
NAME = 'rxv5e16a901'


class Fake:
    temp_index = staticmethod(trial.PiOps.temp_index)
    public = staticmethod(trial.PiOps.public)

    def __init__(self, fail=None, partial=False):
        self.fail, self.partial = fail, partial
        self.commands = []
        self.cleanup = False
        self.s = dict(safe=True, interfaces=['wlan0'], managed=True, nm_state=20,
                      supplicant=[], live='active', capture_process=True,
                      firmware=0, primary_type=1, phy='phy0', temp_index=None,
                      temp_type=None, temp_phy=None, temp_monitor=False,
                      temp_nm_managed=None, primary_ifindex=3, primary_up=True,
                      runtime_fresh=True, runtime_state='running', runtime_error=False,
                      health='degraded')

    def event(self, *_args, **_values):
        pass

    def observe(self, name):
        assert name == NAME
        return copy.deepcopy(self.s)

    def change(self, args):
        self.commands.append(args)
        label = ('stop' if args[:2] == ['systemctl', 'stop'] else
                 'start' if args[:2] == ['systemctl', 'start'] else
                 'managed_' + args[-1] if args[0] == 'nmcli' else
                 'create' if args[:2] == ['iw', 'phy'] else
                 'delete' if args[-1] == 'del' else args[-1])
        failure = label == self.fail
        if failure and not self.partial:
            raise trial.Stop('synthetic ' + label)
        if label == 'stop':
            self.s.update(live='inactive', capture_process=False)
        elif label == 'start':
            self.s.update(live='active', capture_process=True)
        elif label.startswith('managed_'):
            value = args[-1] == 'yes'
            self.s.update(managed=value, nm_state=20 if value else 10)
        elif label == 'create':
            self.s.update(interfaces=['wlan0', NAME], temp_index=9, temp_phy='phy0',
                          temp_monitor=True, temp_type=803, temp_nm_managed=False)
        elif label == 'up':
            self.s['firmware'] = 2
        elif label == 'down':
            self.s['firmware'] = 0
        elif label == 'delete':
            self.s.update(interfaces=['wlan0'], temp_index=None)
        if failure:
            raise trial.Stop('synthetic partial ' + label)

    def sample(self, name, arphrd, *, seconds):
        assert seconds == 60 and arphrd == 803 and name == NAME
        if self.fail == 'sample':
            raise trial.Stop('synthetic sample')
        return {'header_counts': {'management': 2}, 'persistent_capture': False}

    def begin_cleanup(self):
        self.cleanup = True

    def restored_ownership(self, name, before):
        return self.observe(name)

    def verify_live(self, name, before):
        assert self.s['live'] == 'active'
        return self.public(self.s)


def test_no_approval_and_invalid_baseline_never_change_hardware():
    ops = Fake()
    with pytest.raises(trial.Stop, match='approval'):
        trial.run_trial(ops, NAME)
    assert ops.commands == []
    for field, value in [('safe', False), ('managed', None), ('supplicant', ['wlan0']),
                         ('runtime_fresh', False), ('runtime_error', True)]:
        ops = Fake()
        ops.s[field] = value
        with pytest.raises(trial.Stop, match='baseline'):
            trial.run_trial(ops, NAME, approved=True)
        assert ops.commands == []


def test_successful_trial_has_one_create_and_exact_narrow_rollback():
    ops = Fake()
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is True
    assert result['test']['arphrd'] == 803
    assert ops.commands == [
        ['systemctl', 'stop', 'redux-live.service'],
        ['nmcli', '--wait', '5', 'device', 'set', 'wlan0', 'managed', 'no'],
        ['iw', 'phy', 'phy0', 'interface', 'add', NAME, 'type', 'monitor'],
        ['ip', 'link', 'set', 'dev', NAME, 'up'],
        ['ip', 'link', 'set', 'dev', NAME, 'down'],
        ['iw', 'dev', NAME, 'del'],
        ['nmcli', '--wait', '5', 'device', 'set', 'wlan0', 'managed', 'yes'],
        ['systemctl', 'start', 'redux-live.service']]


@pytest.mark.parametrize('failure', ['stop', 'managed_no', 'create', 'up', 'sample'])
@pytest.mark.parametrize('partial', [False, True])
def test_partial_failures_cleanup_once_without_driver_or_manager_restart(failure, partial):
    ops = Fake(failure, partial)
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is True
    assert ops.s['interfaces'] == ['wlan0'] and ops.s['firmware'] == 0
    assert ops.s['managed'] is True and ops.s['live'] == 'active'
    assert sum(cmd[:2] == ['iw', 'phy'] for cmd in ops.commands) <= 1
    assert not any(cmd[0] in ('modprobe', 'reboot') or 'restart' in cmd for cmd in ops.commands)
    assert all('eth0' not in cmd for cmd in ops.commands)


@pytest.mark.parametrize('failure', ['down', 'delete', 'managed_yes', 'start'])
def test_failed_cleanup_never_claims_restoration_or_retries(failure):
    ops = Fake(failure)
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is False
    assert result['cleanup']
    assert sum(cmd == ['systemctl', 'start', 'redux-live.service'] for cmd in ops.commands) <= 1


def test_manager_claiming_temporary_vif_aborts_before_activation():
    class Claimed(Fake):
        def change(self, args):
            super().change(args)
            if args[:2] == ['iw', 'phy']:
                self.s['temp_nm_managed'] = True
    ops = Claimed()
    result = trial.run_trial(ops, NAME, approved=True)
    assert 'stopped' in result['test'] and result['restored']
    assert ['ip', 'link', 'set', 'dev', NAME, 'up'] not in ops.commands


def test_replaced_temporary_interface_is_never_deleted():
    class Replaced(Fake):
        def begin_cleanup(self):
            super().begin_cleanup()
            self.s['temp_index'] = 999
    ops = Replaced()
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is False
    assert ['iw', 'dev', NAME, 'del'] not in ops.commands


def test_unsafe_route_or_regulatory_drift_blocks_service_restore():
    class Drift(Fake):
        def begin_cleanup(self):
            super().begin_cleanup()
            self.s['safe'] = False
    ops = Drift()
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is False
    assert ops.s['live'] == 'inactive'
    assert ['systemctl', 'start', 'redux-live.service'] not in ops.commands


@pytest.mark.parametrize('arphrd', [801, 803])
def test_passive_decoder_counts_mac_headers_without_returning_identifiers(arphrd):
    header = b'\x80\x00' + b'\x00' * 34  # synthetic complete beacon fixed header
    packet = (b'\0\0' + struct.pack('<H', 8) + b'\0'*4 + header) if arphrd == 803 else header
    assert trial.decode_header(packet, arphrd) == 'management'
    for prefix in range(len(packet)):
        assert trial.decode_header(packet[:prefix], arphrd) == 'invalid'
    assert trial.decode_header(packet, 1) == 'invalid'


def test_sampler_ignores_outgoing_packets_and_has_no_transmit_api(monkeypatch):
    now = [0.0]
    packet = b'\0\0\x08\0' + b'\0'*4 + b'\x80\0' + b'\0'*34
    class ReceiveOnly:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def bind(self, address): assert address == (NAME, 0)
        def setblocking(self, value): assert value is False
        def recvfrom(self, limit):
            assert limit == 65535
            outgoing = now[0] < 1
            now[0] += 31
            return packet, (NAME, 0, 4 if outgoing else 0)
    monkeypatch.setattr(trial.socket, 'socket', lambda *_: ReceiveOnly())
    monkeypatch.setattr(trial.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(trial.select, 'select', lambda readable, *_: (readable, [], []))
    ops = object.__new__(trial.PiOps)
    ops.deadline = 210
    ops.observe = lambda _name: dict(safe=True, managed=False, supplicant=[],
                                    temp_nm_managed=False, temp_type=803,
                                    firmware=2, temp_index=9)
    result = ops.sample(NAME, 803, seconds=999)
    assert result['header_counts']['management'] == 1
    assert sum(result['header_counts'].values()) == 1
    assert result['persistent_capture'] is False


def test_session_interrupt_runs_the_same_narrow_cleanup():
    class Interrupted(Fake):
        def sample(self, *_args, **_kwargs):
            raise KeyboardInterrupt
    ops = Interrupted()
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is True and ops.cleanup
    assert result['test']['stopped'] == 'KeyboardInterrupt'


def test_unsafe_primary_flags_after_manager_restore_prevent_live_start():
    class ChangedPrimary(Fake):
        def change(self, args):
            super().change(args)
            if args[0] == 'nmcli' and args[-1] == 'yes':
                self.s['primary_up'] = False
    ops = ChangedPrimary()
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is False
    assert ['systemctl', 'start', 'redux-live.service'] not in ops.commands


def test_sampler_detects_new_manager_claim_before_reading_frames(monkeypatch):
    class NoRead:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def bind(self, *_): pass
        def setblocking(self, *_): pass
        def recvfrom(self, *_): pytest.fail('must not read after isolation fails')
    monkeypatch.setattr(trial.socket, 'socket', lambda *_: NoRead())
    ops = object.__new__(trial.PiOps)
    ops.deadline = trial.time.monotonic() + 210
    calls = []
    def observe(_name):
        calls.append(True)
        return dict(safe=True, managed=False, supplicant=[], temp_nm_managed=len(calls)>1,
                    temp_type=803, firmware=2, temp_index=9)
    ops.observe = observe
    with pytest.raises(trial.Stop, match='isolation changed'):
        ops.sample(NAME, 803, seconds=60)



def test_singleton_guard_blocks_parallel_trials_without_a_lock_file():
    import sys
    if sys.platform != "linux":
        pytest.skip("Linux abstract Unix socket")
    with trial.single_trial_lock():
        with pytest.raises(trial.Stop, match="another trial"):
            with trial.single_trial_lock():
                pytest.fail("second trial must not enter")
    with trial.single_trial_lock():
        pass
