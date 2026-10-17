"""Synthetic files/commands only: no hardware or receive qualification."""
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from redux.radio import local_evidence as e
from redux.radio.live_lease import FileLeaseEvidence, UsbLeaseGuard, LeaseLost

PHY = 'Wiphy phy1\n\tSupported interface modes:\n\t\t * managed\n\t\t * monitor\n\tBand 1:\n\t\t * 2412 MHz\n'
DEV = 'phy#1\n\tInterface usbtest\n\t\tifindex 9\n\t\ttype monitor\n'


@pytest.fixture
def measured(tmp_path):
    sys = tmp_path/'net'; sys.mkdir()
    device = tmp_path/'devices'/'usb1'/'port'; device.mkdir(parents=True)
    bus = tmp_path/'bus'/'usb'; bus.mkdir(parents=True)
    (device/'subsystem').symlink_to(bus)
    phy = tmp_path/'phy1'; phy.mkdir()
    net = sys/'usbtest'; net.mkdir()
    (net/'device').symlink_to(device)
    (net/'phy80211').symlink_to(phy)
    for name, text in [('ifindex','9'), ('type','803'), ('flags','0x1')]:
        (net/name).write_text(text)
    proc = tmp_path/'proc'; (proc/'sys/kernel/random').mkdir(parents=True)
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    (proc/'sys/kernel/random/boot_id').write_text(boot)
    calls = []
    def run(args):
        calls.append(tuple(args))
        if args == ['iw','phy']: return PHY
        if args == ['iw','dev']: return DEV
        if args[:4] == ['ip','-j','route','get']: return '[{"dev":"eth0","prefsrc":"192.0.2.2"}]'
        if args[:3] == ['ip','-j','addr']:
            return '[{"ifname":"eth0","ifindex":2,"flags":["UP"],"addr_info":[{"family":"inet","local":"192.0.2.2","prefixlen":24}]}]'
        raise e.Unavailable('synthetic_unknown')
    class Verifier:
        def __init__(self,*a,**kw): pass
        def __call__(self,*a): pass
    def collect(**kw):
        return e.collect('192.0.2.1', run=run, sys_root=sys, proc_root=proc,
                         verifier_factory=Verifier, **kw)
    return sys, proc, run, collect, calls


def test_independent_receipt_never_invents_reception_or_authentication(measured):
    checks = {}
    receipt = measured[3](ownership_checks=checks)
    row = receipt['radios'][0]
    assert row == dict(iface='usbtest', phy='phy1', ifindex=9, monitor=True, bands=['2.4'],
        mode='monitor', arphrd=803, decoded_frames=None, owner_verified=False,
        owner='unknown', transport='usb')
    assert receipt['ethernet_verified'] is True
    # Even an all-passing technical exclusion check cannot authenticate owner.
    assert checks == ({'usbtest': True} if os.geteuid() == 0 else {})
    assert all(e.BoundedReader.allowed(a) for a in measured[4])


@pytest.mark.parametrize('mutation', ['disappear','replace','index','boot','mode','process'])
def test_mid_observation_identity_changes_abort(measured, mutation):
    sys, proc, run, _, _ = measured
    seen = [0]
    def racing(args):
        value = run(args)
        if args[:3] == ['ip','-j','addr']:
            seen[0] += 1
            if seen[0] == 1:
                if mutation == 'disappear': (sys/'usbtest'/'phy80211').unlink()
                if mutation == 'replace':
                    (sys/'usbtest').rename(sys/'old'); (sys/'old').rename(sys/'usbtest')
                    (sys/'usbtest'/'ifindex').write_text('10')
                if mutation == 'index': (sys/'usbtest'/'ifindex').write_text('11')
                if mutation == 'boot': (proc/'sys/kernel/random/boot_id').write_text('replaced')
                if mutation == 'process':
                    p = proc/'200'; p.mkdir(); (p/'comm').write_text('bettercap')
                    (p/'stat').write_text('200 (bettercap) S '+'0 '*18+'123 0')
                    (p/'cgroup').write_text('0::/foreign')
        if mutation == 'mode' and args == ['iw','dev'] and seen[0]:
            value = value.replace('monitor','managed')
        return value
    with pytest.raises((e.Unavailable, OSError)):
        e.collect('192.0.2.1',run=racing,sys_root=sys,proc_root=proc)


@pytest.mark.parametrize('delta', [3.01,-0.01,float('nan'),float('inf')])
def test_stale_or_regressing_collection_rejected(measured, delta):
    moments = iter([100,100+delta])
    with pytest.raises(e.Unavailable, match='stale'):
        measured[3](clock=lambda: next(moments))


def test_unknown_capability_is_not_driver_inferred(measured):
    sys, proc, run, _, _ = measured
    def missing(args):
        return 'Wiphy phy1\n' if args == ['iw','phy'] else run(args)
    with pytest.raises(e.Unavailable, match='capabilities_unknown'):
        e.collect('192.0.2.1',run=missing,sys_root=sys,proc_root=proc)


def test_permission_failure_never_emits_receipt(measured, monkeypatch):
    monkeypatch.setattr(e, 'inventory', lambda *a: (_ for _ in ()).throw(PermissionError('private path')))
    with pytest.raises(PermissionError): measured[3]()


@pytest.mark.parametrize('args', [
    ['iw','dev','usbtest','set','type','monitor'], ['ip','link','set','usbtest','up'],
    ['nmcli','device','set','usbtest','managed','no'], ['systemctl','restart','redux-live'],
    ['tcpdump','-i','usbtest'], ['busctl','--system','call','org.bluez','/','x','Pair'],
    ['bash','-c','true'], ['iw','dev','../private','info'], ['ip','-j','route','get','hostname'],
])
def test_mutation_packet_and_unbounded_commands_denied(args):
    assert not e.BoundedReader.allowed(args)
    with pytest.raises(e.Unavailable, match='command_denied'): e.BoundedReader()(args)


def test_deadline_prevents_spawn():
    moments=iter([100,101])
    reader = e.BoundedReader(seconds=.1,clock=lambda:next(moments))
    with pytest.raises(e.Unavailable, match='deadline'): reader(['iw','dev'])


@pytest.mark.parametrize('mode', ['overflow','timeout','failure'])
def test_command_hard_bounds_and_sanitized_errors(monkeypatch, mode):
    original = subprocess.Popen
    commands = {'overflow':['python3','-c',"print('x'*140000)"],
                'timeout':['python3','-c','import time;time.sleep(5)'],
                'failure':['python3','-c',"raise Exception('PRIVATE')"]}
    children=[]
    def child(args, **kw):
        p = original(commands[mode], **kw); children.append(p); return p
    monkeypatch.setattr(e.subprocess,'Popen',child)
    with pytest.raises(e.Unavailable) as error:
        e.BoundedReader(seconds=.2)(['iw','dev'])
    assert 'PRIVATE' not in str(error.value)
    assert children[0].poll() is not None


@pytest.fixture
def root_receipts(monkeypatch):
    """Synthetic root metadata; no privileged filesystem operation."""
    original_lstat=Path.lstat
    original_fstat=os.fstat
    def root_stat(value):
        parts=list(value);parts[4]=0
        return os.stat_result(parts)
    monkeypatch.setattr(e.os,'geteuid',lambda:0)
    monkeypatch.setattr(Path,'lstat',lambda p,*a,**kw:root_stat(original_lstat(p,*a,**kw)))
    monkeypatch.setattr(e.os,'fstat',lambda fd:root_stat(original_fstat(fd)))


def test_atomic_receipt_exact_reader_contract_and_guard_refuses_unqualified(measured, tmp_path, root_receipts):
    receipt=measured[3](); path=tmp_path/'receipt.json'
    e.publish(receipt,path)
    assert path.stat().st_mode & 0o777 == 0o600
    observation=FileLeaseEvidence(path)()
    assert observation.radios[0].evidence.decoded_frames is None
    guard=UsbLeaseGuard(FileLeaseEvidence(path),lambda *a: None)
    with pytest.raises(LeaseLost): guard.acquire('usbtest')
    assert guard.blocked
    assert not list(tmp_path.glob('.usb-evidence-*'))


@pytest.mark.parametrize('fault',['stale','oldboot','nonroot','writable','symlink','empty','fabricated_rx'])
def test_writer_refuses_unsafe_receipts_and_preserves_existing(measured,tmp_path,monkeypatch,fault,root_receipts):
    receipt=measured[3](); parent=tmp_path/'receipts'; parent.mkdir(mode=0o700)
    target=parent/'evidence'; target.write_text('previous diagnostic')
    if fault=='stale': receipt['sampled_monotonic']-=4
    if fault=='oldboot': receipt['boot_id']='old'
    if fault=='nonroot': monkeypatch.setattr(e.os,'geteuid',lambda:1000)
    if fault=='writable': parent.chmod(0o777)
    if fault=='symlink':
        link=tmp_path/'link';link.symlink_to(parent);target=link/'evidence'
    if fault=='empty': receipt['radios']=[]
    if fault=='fabricated_rx': receipt['radios'][0]['decoded_frames']=1
    with pytest.raises(e.Unavailable): e.publish(receipt,target)
    assert target.read_text()=='previous diagnostic'


def test_atomic_write_failure_cleans_only_own_temporary(measured,tmp_path,monkeypatch,root_receipts):
    path=tmp_path/'receipt';path.write_text('old')
    monkeypatch.setattr(e.os,'replace',lambda *a,**k: (_ for _ in ()).throw(OSError('failure')))
    with pytest.raises(OSError):e.publish(measured[3](),path)
    assert path.read_text()=='old'
    assert not list(tmp_path.glob('.usb-evidence-*'))


def test_real_exclusion_backend_commands_fit_readonly_transport(tmp_path):
    from test_live_usb_lease import kernel_fixture
    verifier, lease, _, _, _, _ = kernel_fixture(tmp_path)
    original=verifier._command
    def bounded(*args):
        assert e.BoundedReader.allowed(args), args
        return original(*args)
    verifier._command=bounded
    verifier(lease,None)


@pytest.mark.parametrize('field,value',[('monitor','yes'),('ifindex',True),('arphrd',True),
    ('decoded_frames',True),('decoded_frames',-1),('owner_verified','yes'),('phy','../secret'),
    ('iface','../secret'),('bands',['unknown']),('transport','driver_guess')])
def test_strict_receipt_schema_rejects_truthiness_and_invalid_identity(measured,field,value):
    receipt=measured[3]();receipt['radios'][0][field]=value
    with pytest.raises(ValueError):e.validate_receipt(receipt,receipt['boot_id'])


def test_duplicate_indices_and_names_cannot_become_leases(measured):
    receipt=measured[3]();receipt['radios']*=2
    with pytest.raises(ValueError):e.validate_receipt(receipt,receipt['boot_id'])


def test_nm_missing_enumeration_is_unknown_not_unmanaged(measured):
    details={};receipt=measured[3](diagnostics=details)
    assert details['usbtest']['nm_managed'] is None
    assert details['usbtest']['nm_state'] is None
    assert receipt['radios'][0]['owner_verified'] is False


def test_pid_reuse_during_observation_rejected(measured):
    sys,proc,run,_,_=measured
    p=proc/'200';p.mkdir();(p/'comm').write_text('wpa_supplicant')
    (p/'stat').write_text('200 (wpa_supplicant) S '+'0 '*18+'123 0')
    (p/'cgroup').write_text('0::/system.slice/wpa_supplicant.service')
    def race(args):
        value=run(args)
        if args==['iw','phy']:(p/'stat').write_text('200 (wpa_supplicant) S '+'0 '*18+'124 0')
        return value
    with pytest.raises(e.Unavailable,match='changed'):
        e.collect('192.0.2.1',run=race,sys_root=sys,proc_root=proc)


def test_post_rename_fsync_failure_reports_uncertain_publication(measured,tmp_path,root_receipts,monkeypatch):
    path=tmp_path/'receipt';path.write_text('old')
    original=e.os.fsync
    calls=[0]
    def failing(fd):
        calls[0]+=1
        if calls[0]==2:raise OSError('synthetic failure after rename')
        return original(fd)
    monkeypatch.setattr(e.os,'fsync',failing)
    with pytest.raises(e.Unavailable,match='publication_uncertain'):
        e.publish(measured[3](),path)
    assert json.loads(path.read_text())['radios'][0]['decoded_frames'] is None
    assert not list(tmp_path.glob('.usb-evidence-*'))


def test_same_name_netdev_replacement_even_same_ifindex_is_rejected(measured):
    sys,proc,run,_,_=measured
    fired=[False]
    def racing(args):
        value=run(args)
        if args==['iw','phy'] and not fired[0]:
            fired[0]=True
            old=sys.parent/'old_net';(sys/'usbtest').rename(old)
            net=sys/'usbtest';net.mkdir()
            for child in old.iterdir():
                if child.is_symlink():(net/child.name).symlink_to(child.resolve())
                else:(net/child.name).write_bytes(child.read_bytes())
        return value
    with pytest.raises(e.Unavailable,match='radio_inventory_changed'):
        e.collect('192.0.2.1',run=racing,sys_root=sys,proc_root=proc)


@pytest.mark.parametrize('output',['[{"dev":"eth0","type":"blackhole","prefsrc":"192.0.2.2"}]',
    '[{"dev":"eth0","prefsrc":"192.0.2.3"}]','[{"dev":"wlan0","prefsrc":"192.0.2.2"}]'])
def test_route_type_or_unbound_source_cannot_verify_ethernet(measured,output):
    sys,proc,run,_,_=measured
    def bad(args):
        return output if args[:4]==['ip','-j','route','get'] else run(args)
    with pytest.raises(e.Unavailable,match='ethernet_unverified'):
        e.collect('192.0.2.1',run=bad,sys_root=sys,proc_root=proc)
