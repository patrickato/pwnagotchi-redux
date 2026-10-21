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
                      temp_nm_managed=None, primary_ifindex=3, primary_up=True, primary_mode='monitor',
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
                 'primary_monitor' if args[:4] == ['iw', 'dev', 'wlan0', 'set'] else
                 'primary_up' if args[:5] == ['ip', 'link', 'set', 'dev', 'wlan0'] else
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
            if not value:
                # Reproduce the Pi observation on 2026-10-16.
                self.s.update(primary_mode='managed', primary_up=False)
        elif label == 'primary_monitor':
            self.s['primary_mode'] = 'monitor'
        elif label == 'primary_up':
            self.s['primary_up'] = True
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
        ['iw', 'dev', 'wlan0', 'set', 'type', 'monitor'],
        ['ip', 'link', 'set', 'dev', 'wlan0', 'up'],
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


@pytest.mark.parametrize('failure', [
    'down', 'delete', 'primary_monitor', 'primary_up', 'managed_yes', 'start'
])
def test_failed_cleanup_never_claims_restoration_or_retries(failure):
    ops = Fake(failure)
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is False
    assert result['cleanup']
    assert sum(cmd == ['systemctl', 'start', 'redux-live.service'] for cmd in ops.commands) <= 1


def test_networkmanager_absent_temp_device_is_unknown_not_unmanaged():
    ops = object.__new__(trial.PiOps)
    ops.command = lambda args, **kw: (0, "wlan0\n")
    assert ops.nm(NAME, absent_ok=True) == (None, None)
    with pytest.raises(trial.Stop, match='original NM device missing'):
        ops.nm(NAME)


def _down_observer(monkeypatch, owners):
    monkeypatch.setattr(trial.time, 'sleep', lambda _seconds: None)
    ops=object.__new__(trial.DownOnlyPiOps)
    ops.deadline=trial.time.monotonic()+30
    ops.command_limit=6
    state=dict(safe=True,managed=False,nm_state=10,supplicant=[],
        primary_mode='managed',primary_up=False,primary_type=1,live='inactive',
        capture_process=False,temp_index=5,temp_phy='phy0',temp_monitor=True,
        temp_up=False,temp_nm_managed=None,firmware=0,temp_type=803,
        interfaces=['wlan0',NAME])
    ops.observe=lambda _name:dict(state)
    ops.event=lambda *_args,**_kwargs:None
    owner_values=iter(owners)
    def command(args,required=True):
        if 'GetNameOwner'in args:return 0,'s "'+next(owner_values)+'"\n'
        if args[-1]in ('GetDevices','GetAllDevices'):return 0,'ao 0\n'
        if args[0]=='udevadm':return 0,'DEVPATH=/private/not-published\n'
        raise AssertionError(args)
    ops.command=command
    return ops


def test_down_only_observation_pins_nm_owner_and_preserves_absence_unknown(monkeypatch):
    from redux.radio.nm_ownership import assess_down_only_samples
    ops=_down_observer(monkeypatch,[':1.20']*4)
    samples=ops.observe_down(NAME,5,'phy0')
    assert len(samples)==2 and all(row['nm_owner_stable']for row in samples)
    evidence=assess_down_only_samples(samples)
    assert evidence.status=='not_enumerated_unknown'
    assert evidence.permits_activation is False
    assert 'private'not in repr(samples)


def test_down_only_observation_rejects_nm_restart_during_snapshot(monkeypatch):
    ops=_down_observer(monkeypatch,[':1.20',':1.21'])
    with pytest.raises(trial.Stop,match='D-Bus owner changed'):
        ops.observe_down(NAME,5,'phy0')


def test_unobserved_temp_networkmanager_ownership_blocks_activation():
    class NotEnumerated(Fake):
        def change(self, args):
            super().change(args)
            if args[:2] == ['iw', 'phy']:
                self.s['temp_nm_managed'] = None
    ops = NotEnumerated()
    result = trial.run_trial(ops, NAME, approved=True)
    assert 'stopped' in result['test']
    assert 'temporary interface isolation not confirmed' in result['test']['stopped']
    assert ['ip', 'link', 'set', 'dev', NAME, 'up'] not in ops.commands
    assert result['restored'] is True


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


@pytest.mark.parametrize(('field', 'value', 'check', 'restored'), [
    ('temp_nm_managed', None, 'temporary_nm_unmanaged', True),
    ('temp_nm_managed', True, 'temporary_nm_unmanaged', True),
    ('temp_type', 1, 'native_80211_link_type', True),
    ('temp_monitor', False, 'temporary_mode_monitor', False),
    ('temp_phy', 'phy9', 'temporary_phy_matches', False),
    ('firmware', 2, 'firmware_mode_expected', True),
])
def test_post_create_failure_reports_exact_bounded_gate_before_cleanup(field, value, check, restored):
    class ChangedVif(Fake):
        def change(self, args):
            super().change(args)
            if args[:2] == ['iw', 'phy']:
                self.s[field] = value
    ops = ChangedVif()
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is restored
    assert 'temporary interface isolation not confirmed' in result['test']['stopped']
    assert check in result['post_create']['failed_checks']
    assert result['post_create']['stage'] == 'created'
    assert result['post_create']['observed']['temp_type'] in (1, 803)
    assert ['ip', 'link', 'set', 'dev', NAME, 'up'] not in ops.commands
    if restored:
        assert result['after']['managed'] is True
        assert ['iw', 'dev', NAME, 'del'] in ops.commands
    else:
        # Unproven monitor/PHY identity must never authorize deletion or
        # subsequent primary/NM/service recovery, even under our test name.
        assert result['cleanup'] == ['temporary interface identity changed; refuse deletion']
        assert 'after' not in result
        assert ['ip', 'link', 'set', 'dev', NAME, 'down'] not in ops.commands
        assert ['iw', 'dev', NAME, 'del'] not in ops.commands
        assert ['iw', 'dev', 'wlan0', 'set', 'type', 'monitor'] not in ops.commands
        assert not any(cmd[0] == 'nmcli' and cmd[-1] == 'yes' for cmd in ops.commands)
        assert ['systemctl', 'start', 'redux-live.service'] not in ops.commands
        assert trial.trial_exit_code(result, execution=True) == 3


def test_post_activate_snapshot_retained_on_driver_mode_failure():
    class BadActivation(Fake):
        def change(self, args):
            super().change(args)
            if args == ['ip', 'link', 'set', 'dev', NAME, 'up']:
                self.s['firmware'] = 0
    ops = BadActivation()
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is True
    assert 'native monitor activation did not pass' in result['test']['stopped']
    assert result['post_create']['failed_checks'] == []
    assert result['post_activate']['failed_checks'] == ['firmware_mode_expected']
    assert result['post_activate']['observed']['firmware'] == 0


def test_valid_vif_snapshots_only_allowlist_never_arbitrary_identifiers():
    ops = Fake()
    ops.s.update(ssid='PRIVATE NETWORK', connection='private-profile',
                 mac_address='00:11:22:33:44:55')
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is True
    for key in ('post_create', 'post_activate'):
        snapshot = result[key]
        assert snapshot['failed_checks'] == []
        assert set(snapshot['observed']) == {
            'safe', 'managed', 'nm_state', 'primary_mode', 'primary_up',
            'primary_type', 'live', 'capture_process', 'firmware',
            'temp_index', 'temp_type', 'temp_monitor', 'temp_nm_managed', 'temp_up',
            'temp_present', 'temp_phy_matches',
        }
        assert snapshot['observed']['temp_type'] == 803
        assert snapshot['observed']['temp_index'] == 9
        assert 'ssid' not in snapshot['observed']
        assert 'connection' not in snapshot['observed']
        assert 'mac_address' not in snapshot['observed']
        assert 'PRIVATE NETWORK' not in repr(snapshot)
        assert '00:11:22:33:44:55' not in repr(snapshot)


def test_snapshot_separates_temporary_interface_identity_without_free_text():
    state = Fake().s
    state.update(interfaces=['wlan0', NAME], temp_index=9, temp_phy='phy0',
                 temp_monitor=True, temp_nm_managed=False, temp_type=803,
                 firmware=0, managed=False, nm_state=10, primary_mode='managed',
                 primary_up=False, live='inactive', capture_process=False)
    snapshot = trial.vif_gate_snapshot(state, NAME, 'phy0', stage='created')
    assert snapshot['failed_checks'] == []
    state['temp_phy'] = 'phy9'
    mismatch = trial.vif_gate_snapshot(state, NAME, 'phy0', stage='created')
    assert mismatch['failed_checks'] == ['temporary_phy_matches']
    assert mismatch['observed']['temp_phy_matches'] is False
    assert 'phy9' not in repr(mismatch)
    state['temp_phy'] = 'phy0'
    state['temp_index'] = 13
    activated = trial.vif_gate_snapshot(state, NAME, 'phy0', stage='activated',
                                        expected_index=9)
    assert 'temporary_ifindex_unchanged' in activated['failed_checks']


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
    ops.observe = lambda _name: dict(safe=True, managed=False, nm_state=10,
                                    primary_mode='managed', primary_up=False, primary_type=1,
                                    supplicant=[], live='inactive', capture_process=False, temp_nm_managed=False, temp_type=803,
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
        return dict(safe=True, managed=False, nm_state=10,
                    primary_mode='managed', primary_up=False, primary_type=1,
                    supplicant=[], live='inactive', capture_process=False, temp_nm_managed=len(calls)>1,
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



def test_unrelated_safety_drift_still_aborts_even_with_expected_nm_side_effect():
    # Hardware-observed transition: managed=no also took wlan0 down and
    # changed its kernel mode. It is NOT safe to assume a flag-only change.
    class PrimaryTransition(Fake):
        def change(self, args):
            super().change(args)
            if args[0] == 'nmcli' and args[-1] == 'no':
                self.s.update(primary_mode='managed', primary_up=False, safe=False)
    ops = PrimaryTransition()
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is False
    assert result['isolation']['managed'] is False
    assert result['isolation']['primary_mode'] == 'managed'
    assert result['cleanup_observed']['primary_up'] is False
    assert 'expected managed/DOWN isolation not confirmed' in result['test']['stopped']
    assert not any(cmd[:2] == ['iw', 'phy'] for cmd in ops.commands)
    assert ['systemctl', 'start', 'redux-live.service'] not in ops.commands
    assert ['nmcli', '--wait', '5', 'device', 'set', 'wlan0', 'managed', 'yes'] not in ops.commands


@pytest.mark.parametrize(('result', 'expected'), [
    ({'restored': True, 'test': {'arphrd': 803, 'reception': {'header_counts': {}}}}, 0),
    ({'restored': True, 'test': {'stopped': 'runtime isolation not confirmed'}}, 2),
    ({'restored': False, 'test': {'stopped': 'rollback blocked'}}, 3),
    ({'restored': False, 'test': {'arphrd': 803, 'reception': {}}}, 3),
    ({'restored': True, 'test': 'not_started'}, 2),
    ({'restored': True, 'test': {}}, 2),
])
def test_native_vif_exit_status_distinguishes_trial_and_rollback(result, expected):
    assert trial.trial_exit_code(result, execution=True) == expected


def test_readonly_preflight_exit_status_reports_unready_gate():
    assert trial.trial_exit_code({'prerequisites_satisfied': True}) == 0
    assert trial.trial_exit_code({'prerequisites_satisfied': False}) == 2
    assert trial.trial_exit_code({}) == 2


@pytest.mark.parametrize('stage', ['create', 'up'])
def test_returning_capture_owner_aborts_without_primary_or_manager_changes(stage):
    class ReturningOwner(Fake):
        def change(self, args):
            super().change(args)
            if (stage == 'create' and args[:2] == ['iw', 'phy']) or (
                    stage == 'up' and args == ['ip', 'link', 'set', 'dev', NAME, 'up']):
                self.s.update(live='active', capture_process=True)
    ops = ReturningOwner()
    result = trial.run_trial(ops, NAME, approved=True)
    assert 'stopped' in result['test'] and result['restored'] is False
    assert ['iw', 'dev', NAME, 'del'] in ops.commands
    assert ['iw', 'dev', 'wlan0', 'set', 'type', 'monitor'] not in ops.commands
    assert not any(cmd[0] == 'nmcli' and cmd[-1] == 'yes' for cmd in ops.commands)
    if stage == 'create':
        assert ['ip', 'link', 'set', 'dev', NAME, 'up'] not in ops.commands


@pytest.mark.parametrize(('field', 'value'), [
    ('interfaces', ['wlan0', 'unexpected']), ('firmware', 2),
    ('primary_type', 803), ('primary_ifindex', 999),
])
def test_post_nm_radio_drift_blocks_live_start(field, value):
    class Drift(Fake):
        def change(self, args):
            super().change(args)
            if args[0] == 'nmcli' and args[-1] == 'yes':
                self.s[field] = value
    ops = Drift()
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is False
    assert ['systemctl', 'start', 'redux-live.service'] not in ops.commands


@pytest.mark.parametrize('failure', ['down', 'delete', 'primary_monitor', 'primary_up', 'managed_yes', 'start'])
def test_partial_cleanup_failure_is_not_retried(failure):
    ops = Fake(failure, partial=True)
    result = trial.run_trial(ops, NAME, approved=True)
    # Down/delete errors may still leave a conclusively restored baseline;
    # errors in later restoration commands stop without further mutations.
    assert result['restored'] is (failure in ('down', 'delete'))
    assert len(ops.commands) == len({tuple(cmd) for cmd in ops.commands})


def test_uncertain_unmanaged_state_blocks_primary_restoration():
    class Uncertain(Fake):
        def begin_cleanup(self):
            super().begin_cleanup()
            self.s['nm_state'] = 30
    ops = Uncertain()
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is False
    assert ['iw', 'dev', 'wlan0', 'set', 'type', 'monitor'] not in ops.commands


def test_subprocess_timeout_is_sanitized_and_deadline_prevents_invocation(monkeypatch):
    ops = object.__new__(trial.PiOps)
    ops.deadline = trial.time.monotonic() + 10
    def timeout(*args, **kwargs):
        assert 0 < kwargs['timeout'] <= 6
        raise trial.subprocess.TimeoutExpired('private-command', 6, stderr='private-data')
    monkeypatch.setattr(trial.subprocess, 'run', timeout)
    with pytest.raises(trial.Stop, match='bounded command unavailable or timed out'):
        ops.command(['synthetic'])
    ops.deadline = 0
    with pytest.raises(trial.Stop, match='phase deadline reached'):
        ops.command(['synthetic'])


@pytest.mark.parametrize('stage', ['primary_monitor', 'primary_up'])
@pytest.mark.parametrize(('field', 'value'), [
    ('live', 'active'), ('capture_process', True), ('nm_state', 30), ('primary_type', 803),
])
def test_primary_restoration_rechecks_exclusive_ownership_after_each_step(stage, field, value):
    class Drift(Fake):
        def change(self, args):
            super().change(args)
            if (stage == 'primary_monitor' and args == ['iw', 'dev', 'wlan0', 'set', 'type', 'monitor']) or (
                    stage == 'primary_up' and args == ['ip', 'link', 'set', 'dev', 'wlan0', 'up']):
                self.s[field] = value
    ops = Drift()
    result = trial.run_trial(ops, NAME, approved=True)
    assert result['restored'] is False
    assert not any(cmd[0] == 'nmcli' and cmd[-1] == 'yes' for cmd in ops.commands)
    assert ['systemctl', 'start', 'redux-live.service'] not in ops.commands
    if stage == 'primary_monitor':
        assert ['ip', 'link', 'set', 'dev', 'wlan0', 'up'] not in ops.commands
