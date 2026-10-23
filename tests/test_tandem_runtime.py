"""Synthetic tandem process supervision: NEVER operates a real RF interface."""
from dataclasses import replace
import subprocess

import pytest

from redux.engine.tandem_readiness import plan_measured_tandem
from redux.engine.tandem_runtime import CompanionPaths, TandemCompanion
from redux.radio.dual_radio import RadioEvidence
from redux.radio.live_lease import (LeaseLost, LeaseObservation, ProcessOwner,
                                    UsbLeaseGuard)
from redux.radio.orchestrator import Radio
from redux.radio.split_integration import MeasuredRadio


class FakeChild:
    def __init__(self, pid):
        self.pid = pid
        self.alive = True
        self.unreapable = False
        self.terminated = 0

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.terminated += 1

    def wait(self, timeout):
        if self.unreapable:
            raise subprocess.TimeoutExpired('synthetic', timeout)
        self.alive = False
        return 0


def fixture(tmp_path, monkeypatch):
    onboard = MeasuredRadio(Radio('wlan0', phy='phy0', monitor=True,
                                  bands=frozenset({'2.4'}), onboard=True),
                            RadioEvidence('phy0', 'monitor', 803, 2, True),
                            3, 'onboard', True, 'capture_exclusive')
    external = MeasuredRadio(Radio('wlan1', phy='phy1', monitor=True,
                                   bands=frozenset({'2.4', '5'}), usb_gen=3),
                             RadioEvidence('phy1', 'monitor', 803, 9, True),
                             8, 'usb', True, 'capture_exclusive')
    radios = (onboard, external)
    readiness = plan_measured_tandem(radios, ethernet_management_verified=True,
                                      bettercap_present=True, angryoxide_present=True)
    assert readiness.state == 'radio_qualified'
    state = {'sample': 10., 'now': 10., 'kernel': True, 'mount': ('ext4', 7),
             'radios': radios, 'management': True}
    def check(lease, pid):
        if not state['kernel']:
            raise LeaseLost('synthetic kernel loss')
    guard = UsbLeaseGuard(
        lambda: LeaseObservation(state['radios'], state['sample'], state['management']),
        check, clock=lambda: state['now'])
    proc = tmp_path / 'proc' / '42'
    proc.mkdir(parents=True)
    (proc / 'comm').write_text('bettercap')
    (proc / 'cmdline').write_bytes(b'/usr/bin/bettercap\0-iface\0wlan0\0')
    (proc / 'stat').write_text('42 (bettercap) ' + ' '.join(['S'] + ['0'] * 18 + ['100']))
    peer = ProcessOwner.pin(42, 'bettercap', 'wlan0', proc_root=proc.parent)
    paths = CompanionPaths(tmp_path)
    for directory in (paths.state, paths.active, paths.incoming):
        directory.mkdir()
    monkeypatch.setattr('redux.engine.tandem_runtime.AngryOxideProvider.available',
                        lambda self: True)
    bettercap = FakeChild(42)
    oxide = FakeChild(55)
    launches = []
    def spawn(argv, **kwargs):
        launches.append((list(argv), kwargs))
        return oxide
    owner = TandemCompanion(readiness, bettercap, peer, guard, paths=paths,
                            spawn=spawn, mount_probe=lambda: state['mount'],
                            proc_root=proc.parent)
    return owner, state, guard, bettercap, oxide, launches, paths, proc


def test_explicit_permission_passive_argv_and_closed_file_handoff(tmp_path, monkeypatch):
    owner, state, guard, _, oxide, launches, paths, _ = fixture(tmp_path, monkeypatch)
    with pytest.raises(LeaseLost, match='authorization'):
        owner.start()
    assert not launches and guard.lease is None
    assert owner.start(operator_authorized=True) == 'running'
    assert guard.lease == owner.readiness.usb_lease
    argv, args = launches[0]
    assert argv[argv.index('-i') + 1] == 'wlan1'
    assert argv[argv.index('-o') + 1] == str(owner.output_dir)
    assert '--notransmit' in argv and '-t' not in argv and '--autohunt' not in argv
    assert args['start_new_session'] is True
    assert owner.tick() == 'running'
    (owner.output_dir / 'sample.pcapng').write_bytes(b'synthetic capture')
    (owner.output_dir / 'sample.hc22000').write_bytes(b'synthetic hash')
    (owner.output_dir / 'metadata.csv').write_bytes(b'preserve other child data')
    assert owner.stop()
    assert not oxide.alive and guard.lease is None
    files = sorted(p.name for p in paths.incoming.iterdir())
    assert len(files) == 2 and all('angryoxide-' in name for name in files)
    assert (owner.output_dir / 'metadata.csv').is_file()
    assert owner.handoffs == 2
    with pytest.raises(LeaseLost, match='re-arm'):
        owner.start(operator_authorized=True)
    owner.rearm(operator_authorized=True)
    assert owner.state == 'idle'


def test_peer_lost_after_launch_interlocks_without_restart(tmp_path, monkeypatch):
    owner, _, guard, bettercap, oxide, launches, _, _ = fixture(tmp_path, monkeypatch)
    owner.start(operator_authorized=True)
    bettercap.alive = False
    assert owner.tick() == 'interlocked'
    assert guard.blocked and guard.lease is None and not oxide.alive
    assert owner.tick() == 'interlocked' and len(launches) == 1
    with pytest.raises(LeaseLost):
        owner.rearm()


def test_kernel_loss_and_pid_reuse_both_fail_closed(tmp_path, monkeypatch):
    owner, state, guard, _, oxide, _, _, proc = fixture(tmp_path, monkeypatch)
    owner.start(operator_authorized=True)
    (proc / 'stat').write_text('42 (bettercap) ' + ' '.join(['S'] + ['0'] * 18 + ['101']))
    assert owner.tick() == 'interlocked' and guard.blocked and not oxide.alive
    owner.rearm(operator_authorized=True)
    (proc / 'stat').write_text('42 (bettercap) ' + ' '.join(['S'] + ['0'] * 18 + ['100']))
    oxide.alive = True
    owner.start(operator_authorized=True)
    state['kernel'] = False
    assert owner.tick() == 'interlocked' and not oxide.alive


def test_unreaped_writer_keeps_lease_and_files_unpublished(tmp_path, monkeypatch):
    owner, state, guard, _, oxide, _, paths, _ = fixture(tmp_path, monkeypatch)
    owner.start(operator_authorized=True)
    (owner.output_dir / 'output.pcapng').write_bytes(b'synthetic')
    oxide.unreapable = True
    state['kernel'] = False
    assert owner.tick() == 'termination_pending'
    assert guard.lease is not None and not list(paths.incoming.iterdir())
    with pytest.raises(LeaseLost):
        owner.rearm(operator_authorized=True)
    oxide.unreapable = False
    assert owner.tick() == 'interlocked'
    assert len(list(paths.incoming.iterdir())) == 1
    assert guard.lease is None


def test_handoff_collision_preserves_capture_then_reconciles(tmp_path, monkeypatch):
    owner, _, guard, _, oxide, _, paths, _ = fixture(tmp_path, monkeypatch)
    owner.start(operator_authorized=True)
    source = owner.output_dir / 'artifact.pcapng'
    source.write_bytes(b'unique offline evidence')
    destination = paths.incoming / (owner.output_dir.name + '-artifact.pcapng')
    destination.write_bytes(b'original file')
    assert not owner.stop()
    assert owner.state == 'handoff_blocked' and guard.lease is not None
    assert source.exists() and source.read_bytes() == b'unique offline evidence'
    assert destination.read_bytes() == b'original file'
    destination.rename(paths.incoming / 'original-preserved.pcapng')
    assert owner.stop()
    assert owner.state == 'interlocked' and guard.lease is None
    assert destination.read_bytes() == b'unique offline evidence'


def test_mount_replacement_blocks_handoff_without_rootfs_fallback(tmp_path, monkeypatch):
    owner, state, guard, _, oxide, _, paths, _ = fixture(tmp_path, monkeypatch)
    owner.start(operator_authorized=True)
    (owner.output_dir / 'c.pcapng').write_bytes(b'retain on original mount')
    state['mount'] = ('ext4', 8)
    assert owner.tick() == 'handoff_blocked'
    assert not list(paths.incoming.iterdir()) and guard.lease is not None
    state['mount'] = ('ext4', 7)
    assert owner.stop()
    assert owner.state == 'interlocked'
    assert len(list(paths.incoming.iterdir())) == 1


@pytest.mark.parametrize('mutation', ['wrong_usb', 'active_binding', 'invalid_peer', 'missing_mount'])
def test_preflight_rejects_mismatched_engines_and_unverified_mount(tmp_path, monkeypatch, mutation):
    owner, state, guard, _, oxide, launches, _, _ = fixture(tmp_path, monkeypatch)
    if mutation == 'missing_mount':
        state['mount'] = None
        with pytest.raises(LeaseLost):
            owner.start(operator_authorized=True)
    else:
        ready = owner.readiness
        if mutation == 'wrong_usb':
            ready = replace(ready, usb_lease=replace(ready.usb_lease, ifindex=9))
        if mutation == 'active_binding':
            ready = replace(ready, bindings=tuple(replace(b, passive=False)
                                                   if b.engine == 'angryoxide' else b
                                                   for b in ready.bindings))
        if mutation == 'invalid_peer':
            owner.bettercap_child.pid = 99
        with pytest.raises(LeaseLost):
            TandemCompanion(ready, owner.bettercap_child, owner.bettercap_owner, guard,
                            paths=owner.paths, mount_probe=owner.mount_probe)
    assert not launches and guard.lease is None


def test_tandem_startup_failure_kills_spawned_writer(tmp_path, monkeypatch):
    owner, state, guard, _, oxide, launches, _, _ = fixture(tmp_path, monkeypatch)
    original = guard.verify_kernel
    def fail_after_spawn(lease, pid):
        if pid is not None:
            raise LeaseLost('postspawn process verification failed')
        original(lease, pid)
    guard.verify_kernel = fail_after_spawn
    with pytest.raises(LeaseLost, match='startup blocked'):
        owner.start(operator_authorized=True)
    assert len(launches) == 1 and not oxide.alive
    assert guard.blocked and guard.lease is None

