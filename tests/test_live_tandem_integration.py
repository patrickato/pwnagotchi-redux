"""LiveRuntime/tandem wiring with fake children and fake radio evidence ONLY."""
from types import SimpleNamespace
import subprocess

import pytest

from redux.core.live_runtime import LiveConfig, LiveRuntime
from redux.engine.tandem_readiness import plan_measured_tandem
from redux.engine.tandem_runtime import CompanionPaths, TandemCompanion
from redux.radio.dual_radio import RadioEvidence
from redux.radio.live_lease import LeaseLost, LeaseObservation, ProcessOwner, UsbLeaseGuard
from redux.radio.orchestrator import Radio
from redux.radio.split_integration import MeasuredRadio


class FakeChild:
    def __init__(self, pid, events, label):
        self.pid = pid
        self.events = events
        self.label = label
        self.alive = True
        self.unreapable = False
        self.stdin = None

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.events.append(self.label + '-terminate')

    def kill(self):
        self.events.append(self.label + '-kill')

    def wait(self, timeout):
        if self.unreapable:
            raise subprocess.TimeoutExpired('fake-engine', timeout)
        self.alive = False
        self.events.append(self.label + '-reap')
        return 0


class FakeAugur:
    _sighting_buffer = ()
    _sighting_flush_error = ''
    _sighting_flush_failures = 0

    def __init__(self, events):
        self.events = events
        self.store = SimpleNamespace(close=lambda: self.events.append('sightings-close'))

    def flush_sightings(self):
        self.events.append('sightings-flush')

    def pump(self):
        self.events.append('augur-pump')


def pair(tmp_path, monkeypatch):
    events = []
    bc = FakeChild(42, events, 'bettercap')
    ao = FakeChild(55, events, 'angryoxide')
    config = LiveConfig(state_dir=tmp_path / 'live', active_dir=tmp_path / 'active',
                        capture_dir=tmp_path / 'incoming', enable_web=False,
                        preferred_iface='wlan0')
    live = LiveRuntime(config, clock=lambda: 10., radio_probe=lambda: [],
                       executor=lambda *a, **kw: pytest.fail('no device commands'))
    live.process = bc
    live.state = 'running'
    live.iface = 'wlan0'
    live.started = 10.
    live.last_probe = 10.
    live.augur = FakeAugur(events)
    proc_dir = tmp_path / 'proc' / '42'
    proc_dir.mkdir(parents=True)
    (proc_dir / 'comm').write_text('bettercap')
    (proc_dir / 'cmdline').write_bytes(b'/usr/bin/bettercap\0-iface\0wlan0\0')
    (proc_dir / 'stat').write_text('42 (bettercap) ' + ' '.join(['S'] + ['0'] * 18 + ['100']))
    pinned = ProcessOwner.pin(42, 'bettercap', 'wlan0', proc_root=proc_dir.parent)
    onboard = MeasuredRadio(Radio('wlan0', phy='phy0', monitor=True,
                                  bands=frozenset({'2.4'}), onboard=True),
                            RadioEvidence('phy0', 'monitor', 803, 8, True),
                            3, 'onboard', True, 'capture_exclusive')
    usb = MeasuredRadio(Radio('wlan1', phy='phy1', monitor=True,
                              bands=frozenset({'2.4', '5'}), usb_gen=3),
                        RadioEvidence('phy1', 'monitor', 803, 8, True),
                        8, 'usb', True, 'capture_exclusive')
    readings = (onboard, usb)
    ready = plan_measured_tandem(readings, ethernet_management_verified=True,
                                  bettercap_present=True, angryoxide_present=True)
    assert ready.state == 'radio_qualified'
    state = {'fresh': True, 'kernel': True, 'mount': ('ext4', 7), 'now': 10.}
    guard = UsbLeaseGuard(
        lambda: LeaseObservation(readings, state['now'] if state['fresh'] else 1., True),
        lambda lease, pid: None if state['kernel'] else (_ for _ in ()).throw(LeaseLost('lost')),
        clock=lambda: state['now'])
    paths = CompanionPaths(tmp_path)
    paths.state.mkdir(exist_ok=True)  # LiveRuntime already owns these directories.
    monkeypatch.setattr('redux.engine.tandem_runtime.AngryOxideProvider.available',
                        lambda self: True)
    started = []
    def spawn(argv, **kw):
        started.append((list(argv), kw))
        return ao
    companion = TandemCompanion(ready, bc, pinned, guard, paths=paths,
                                spawn=spawn, mount_probe=lambda: state['mount'],
                                proc_root=proc_dir.parent)
    return live, companion, state, bc, ao, events, started


def test_default_service_does_not_start_second_engine(tmp_path, monkeypatch):
    live, companion, _, bc, ao, events, started = pair(tmp_path, monkeypatch)
    assert live.tandem is None and live._tandem_interlock is False
    with pytest.raises(LeaseLost):
        live.attach_tandem(companion)
    assert not started and ao.alive
    with pytest.raises(LeaseLost):
        live.attach_tandem(companion, operator_authorized=1)
    assert live.tandem is None
    live.close()


def test_explicit_attachment_tick_and_shutdown_order(tmp_path, monkeypatch):
    live, companion, _, bc, ao, events, started = pair(tmp_path, monkeypatch)
    live.attach_tandem(companion, operator_authorized=True)
    assert len(started) == 1 and live.tandem is companion
    assert '--notransmit' in started[0][0]
    assert live.tick() == 'running'
    assert live._snapshot['runtime']['tandem']['state'] == 'running'
    assert live._snapshot['runtime']['tandem']['automatic_restart'] is False
    tandem_findings = [f for f in live._snapshot['doctor']['findings']
                       if f['area'] == 'tandem capture']
    assert len(tandem_findings) == 1 and tandem_findings[0]['status'] == 'attention'
    (companion.output_dir / 'valid.pcapng').write_bytes(b'synthetic')
    assert live._drop()
    assert events.index('angryoxide-reap') < events.index('bettercap-reap')
    assert events.index('bettercap-reap') < events.index('sightings-flush')
    assert len(list(live.config.capture_dir.iterdir())) == 1
    assert live.tandem is None and live._tandem_last['handed_off'] == 1
    assert live._tandem_interlock is False
    live.close()


def test_peer_loss_stops_both_and_requires_explicit_solo_rearm(tmp_path, monkeypatch):
    live, companion, state, bc, ao, events, started = pair(tmp_path, monkeypatch)
    live.attach_tandem(companion, operator_authorized=True)
    state['kernel'] = False
    assert live.tick() == 'tandem_blocked'
    assert not ao.alive and not bc.alive
    assert live.tandem is None and live._tandem_interlock
    assert live._snapshot['runtime']['tandem']['state'] == 'interlocked'
    assert any(f['area'] == 'tandem capture' and f['status'] == 'action'
               for f in live._snapshot['doctor']['findings'])
    assert live.tick() == 'tandem_blocked' and len(started) == 1
    with pytest.raises(LeaseLost):
        live.rearm_tandem()
    live.rearm_tandem(operator_authorized=True)
    assert live.state == 'degraded' and live._tandem_interlock is False
    live.close()


def test_unreapable_usb_child_prevents_restart_and_handoff(tmp_path, monkeypatch):
    live, companion, state, bc, ao, events, started = pair(tmp_path, monkeypatch)
    live.attach_tandem(companion, operator_authorized=True)
    (companion.output_dir / 'valid.pcapng').write_bytes(b'synthetic')
    ao.unreapable = True
    state['kernel'] = False
    assert live.tick() == 'termination_pending'
    assert not bc.alive and ao.alive and companion.usb_guard.lease is not None
    assert not list(live.config.capture_dir.iterdir())
    assert live.tick() == 'termination_pending' and len(started) == 1
    ao.unreapable = False
    assert live.tick() == 'tandem_blocked'
    assert not ao.alive and len(list(live.config.capture_dir.iterdir())) == 1
    assert live.tandem is None
    live.close()


def test_handoff_collision_retains_both_owner_locks_until_reconciled(tmp_path, monkeypatch):
    live, companion, state, bc, ao, events, started = pair(tmp_path, monkeypatch)
    live.attach_tandem(companion, operator_authorized=True)
    source = companion.output_dir / 'collision.pcapng'
    source.write_bytes(b'new evidence')
    destination = live.config.capture_dir / (companion.output_dir.name + '-collision.pcapng')
    destination.write_bytes(b'existing evidence')
    state['kernel'] = False
    assert live.tick() == 'termination_pending'
    assert not bc.alive and not ao.alive
    assert companion.usb_guard.lease is not None and source.is_file()
    assert destination.read_bytes() == b'existing evidence'
    destination.rename(live.config.capture_dir / 'preserved.pcapng')
    assert live.tick() == 'tandem_blocked'
    assert destination.read_bytes() == b'new evidence'
    assert companion.usb_guard.lease is None
    live.close()


def test_live_rejects_companion_for_different_bettercap_instance(tmp_path, monkeypatch):
    live, companion, _, bc, ao, events, started = pair(tmp_path, monkeypatch)
    live.process = FakeChild(43, events, 'other-bettercap')
    with pytest.raises(LeaseLost):
        live.attach_tandem(companion, operator_authorized=True)
    assert not started and live.tandem is None
    live.close()


def test_live_rejects_companion_with_other_storage_handoff(tmp_path, monkeypatch):
    live, companion, _, bc, ao, events, started = pair(tmp_path, monkeypatch)
    companion.paths = CompanionPaths(tmp_path / 'unverified-other-volume')
    with pytest.raises(LeaseLost):
        live.attach_tandem(companion, operator_authorized=True)
    assert not started and live.tandem is None
    live.close()


def test_supervisor_exception_stops_pair_instead_of_crashing_loop(tmp_path, monkeypatch):
    live, companion, _, bc, ao, events, started = pair(tmp_path, monkeypatch)
    live.attach_tandem(companion, operator_authorized=True)
    def broken_tick():
        raise RuntimeError('synthetic owner monitor exception')
    monkeypatch.setattr(companion, 'tick', broken_tick)
    assert live.tick() == 'tandem_blocked'
    assert not bc.alive and not ao.alive
    assert live.observer_errors['tandem_supervision'] == 'RuntimeError'
    assert len(started) == 1
    live.close()


def test_companion_teardown_exception_keeps_reference_and_stops_bettercap(tmp_path, monkeypatch):
    live, companion, _, bc, ao, events, started = pair(tmp_path, monkeypatch)
    live.attach_tandem(companion, operator_authorized=True)
    saved_stop = companion.stop
    def broken_stop():
        raise OSError('synthetic close error')
    monkeypatch.setattr(companion, 'stop', broken_stop)
    assert live._drop() is False
    assert live.state == 'termination_pending' and live.tandem is companion
    assert not bc.alive and ao.alive
    monkeypatch.setattr(companion, 'stop', saved_stop)
    assert live._drop()
    assert not ao.alive and live.tandem is None
    live.close()
