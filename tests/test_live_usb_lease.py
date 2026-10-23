"""Offline live owner integration: no subprocess, sysfs or device writes."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from redux.radio.live_lease import LeaseObservation, UsbLeaseGuard, LeaseLost, FileLeaseEvidence
from redux.radio.split_integration import MeasuredRadio
from redux.radio.orchestrator import Radio
from redux.radio.dual_radio import RadioEvidence
from redux.core.live_runtime import LiveConfig, LiveRuntime
from redux.engine.bettercap_driver import BettercapDriver, ReplayTransport, BettercapUnavailable


def records(index=8):
    return (MeasuredRadio(Radio('wlan0',phy='phy0',monitor=True), RadioEvidence('phy0'),3,'onboard',True),
            MeasuredRadio(Radio('wlan1',phy='phy1',monitor=True),
                          RadioEvidence('phy1','monitor',803,7,True),index,'usb',True,'capture_exclusive'))


def guard_fixture():
    state={'now':10.,'sample':10.,'records':records(),'management':True,'kernel':True}
    def verify(lease,pid):
        if not state['kernel']: raise LeaseLost('synthetic private source')
    guard=UsbLeaseGuard(lambda:LeaseObservation(state['records'],state['sample'],state['management']),
                        verify,clock=lambda:state['now'])
    return guard,state


@pytest.mark.parametrize('fault', ['stale','future','replay','management','index','kernel','owner'])
def test_continuity_fault_latches_and_cannot_retry(fault):
    g,s=guard_fixture()
    g.acquire('wlan1');g.check()
    if fault=='stale':s['now']=14
    if fault=='future':s['sample']=11
    if fault=='replay':s['sample']=9
    if fault=='management':s['management']=False
    if fault=='index':s['records']=records(9)
    if fault=='kernel':s['kernel']=False
    if fault=='owner':s['records']=(s['records'][0],replace(s['records'][1],owner='networkmanager'))
    with pytest.raises(LeaseLost):g.check(owner_pid=42)
    assert g.blocked
    g.release()
    with pytest.raises(LeaseLost):g.acquire('wlan1')
    with pytest.raises(LeaseLost):g.rearm()


def test_lease_generation_pins_across_normal_rotation_and_explicit_rearm():
    g,s=guard_fixture()
    g.acquire('wlan1');g.release()
    s['records']=records(9)
    with pytest.raises(LeaseLost):g.acquire('wlan1')
    g.rearm(owner_authorized=True)
    assert g.acquire('wlan1').ifindex==9


class Child:
    pid=42
    stdin=None
    def __init__(self): self.live=True;self.unreapable=False
    def poll(self): return None if self.live else 0
    def terminate(self):pass
    def kill(self):pass
    def wait(self,timeout):
        if self.unreapable:raise subprocess.TimeoutExpired('synthetic',timeout)
        self.live=False;return 0


def runtime(tmp_path, guard):
    config=LiveConfig(state_dir=tmp_path/'state',active_dir=tmp_path/'active',capture_dir=tmp_path/'incoming',
                      preferred_iface='wlan1',enable_web=False,require_usb_lease=True,
                      usb_evidence_file=tmp_path/'evidence.json',usb_management_peer='192.0.2.1')
    child=Child()
    def no_commands(*a,**kw):pytest.fail('leased runtime must not call iw/ip mode setters')
    r=LiveRuntime(config,lease_guard=guard,radio_probe=lambda:[m.capabilities for m in records()],
                  executor=no_commands,spawn=lambda *a,**kw:child,clock=lambda:10.)
    return r,child


def test_startup_operation_loss_teardown_and_restart_interlock(tmp_path):
    g,s=guard_fixture();r,c=runtime(tmp_path,g)
    r._launch('wlan1')
    assert g.lease.iface=='wlan1'
    r.capture_file.write_bytes(b'closed-synthetic-capture')
    s['kernel']=False
    assert r.tick()=='lease_blocked'
    assert not c.live and g.lease is None
    assert len(list(r.config.capture_dir.glob('*.pcap')))==1
    r.close()
    g2,s2=guard_fixture();r2,c2=runtime(tmp_path,g2)
    assert r2.tick()=='lease_blocked' and r2.engine_attempts==0
    r2.rearm_usb_capture(owner_authorized=True)
    assert not r2._lease_interlock
    r2.close()


def test_unreaped_writer_keeps_lease_and_never_hands_off(tmp_path):
    g,s=guard_fixture();r,c=runtime(tmp_path,g)
    r._launch('wlan1');r.capture_file.write_bytes(b'synthetic')
    c.unreapable=True;s['kernel']=False
    assert r.tick()=='termination_pending'
    assert g.lease is not None and not list(r.config.capture_dir.glob('*'))
    assert r.engine_attempts==1
    c.unreapable=False;r.tick();r.close()


def test_removal_between_spawn_and_startup_stops_before_api(tmp_path):
    g,s=guard_fixture();r,c=runtime(tmp_path,g)
    def race(*a,**kw):s['records']=records(9);return c
    r.spawn=race
    with pytest.raises(LeaseLost):r._launch('wlan1')
    assert r.process is c
    assert r.tick()=='lease_blocked' and not c.live
    r.close()


def test_driver_checks_before_after_and_blocks_reassignment():
    g,s=guard_fixture();g.acquire('wlan1')
    transport=ReplayTransport()
    driver=BettercapDriver(transport=transport,operation_guard=g.check,capture_iface='wlan1')
    driver.recon();assert len(transport.commands)==1
    with pytest.raises(BettercapUnavailable):driver.set_interface('wlan0')
    s['management']=False
    with pytest.raises(LeaseLost):driver.recon()
    assert len(transport.commands)==1


def test_late_api_reply_is_not_consumed_after_lease_loss():
    g,s=guard_fixture();g.acquire('wlan1')
    class Racing(ReplayTransport):
        def events(self,clear=False):s['kernel']=False;return [{'tag':'wifi.ap.new'}]
    driver=BettercapDriver(transport=Racing(),operation_guard=g.check,capture_iface='wlan1')
    with pytest.raises(LeaseLost):driver.poll_events()


def test_live_config_requires_explicit_usb_identity_and_ethernet_peer(tmp_path):
    base=dict(require_usb_lease=True,usb_management_peer='192.0.2.1',usb_evidence_file=tmp_path/'evidence')
    with pytest.raises(ValueError):LiveConfig(**base).validate()
    with pytest.raises(ValueError):LiveConfig(**base,preferred_iface='wlan1',allow_connected_capture=True).validate()
    LiveConfig(**base,preferred_iface='wlan1').validate()


def test_evidence_file_rejects_symlink_fifo_oversize_and_untrusted_permissions(tmp_path):
    real=tmp_path/'real';real.write_text('{}');real.chmod(0o666)
    with pytest.raises(LeaseLost):FileLeaseEvidence(real)()
    link=tmp_path/'link';link.symlink_to(real)
    with pytest.raises(OSError):FileLeaseEvidence(link)()
    real.write_bytes(b'x'*65537);real.chmod(0o600)
    with pytest.raises(LeaseLost):FileLeaseEvidence(real)()


def kernel_fixture(tmp_path):
    from types import SimpleNamespace
    from redux.radio.live_lease import KernelUsbVerifier
    from redux.radio.split_integration import CaptureLease
    sys=tmp_path/'sys';proc=tmp_path/'proc';sys.mkdir();proc.mkdir()
    node=sys/'wlan1';node.mkdir()
    for name,value in [('ifindex','8'),('type','803'),('flags','0x1')]: (node/name).write_text(value)
    usb=tmp_path/'device';usb.mkdir();usb_bus=tmp_path/'usb';usb_bus.mkdir()
    (usb/'subsystem').symlink_to(usb_bus);(node/'device').symlink_to(usb)
    service=proc/'20';service.mkdir();(service/'comm').write_text('wpa_supplicant')
    (service/'cgroup').write_text('0::/system.slice/wpa_supplicant.service')
    state={'dev':'eth0','nm':'no\n10 (unmanaged)\n','info':'type monitor\nwiphy 1\n',
           'devs':'phy#1\n Interface wlan1\n','bus':'ao 0\n'}
    commands=[]
    def run(args,**kwargs):
        commands.append(args)
        assert kwargs['timeout']==2
        if args[:4]==['ip','-j','route','get']: out=json.dumps([{'dev':state['dev']}])
        elif args[0]=='ip':out=json.dumps([{'ifindex':2,'flags':['UP'],'addr_info':[{'local':'192.0.2.2'}]}])
        elif args==['iw','dev']:out=state['devs']
        elif args[0]=='iw':out=state['info']
        elif args[0]=='nmcli':out=state['nm']
        elif args[0]=='busctl':out=state['bus']
        else:pytest.fail('unexpected probe')
        return SimpleNamespace(returncode=0,stdout=out)
    return KernelUsbVerifier('192.0.2.1',run=run,sys_root=sys,proc_root=proc),CaptureLease('wlan1','phy1',8),state,node,proc,commands


def test_independent_kernel_route_nm_bus_process_and_identity_checks(tmp_path):
    verifier,lease,state,node,proc,commands=kernel_fixture(tmp_path)
    verifier(lease,None)
    child=proc/'42';child.mkdir();(child/'comm').write_text('bettercap')
    verifier(lease,42)
    with pytest.raises(LeaseLost):verifier(lease,None)
    assert not any('set' in cmd or cmd[0]=='systemctl' for cmd in commands)


@pytest.mark.parametrize('fault', ['route','unknown_nm','mode','shared_phy','link_down','ifindex','extra_supplicant','generation'])
def test_independent_kernel_failures_never_trust_receipt_alone(tmp_path,fault):
    verifier,lease,state,node,proc,_=kernel_fixture(tmp_path)
    verifier(lease,None)
    if fault=='route':state['dev']='wlan0'
    if fault=='unknown_nm':state['nm']=''
    if fault=='mode':state['info']='type managed\nwiphy 1\n'
    if fault=='shared_phy':state['devs']+=' Interface wlan1mon\n'
    if fault=='link_down':(node/'flags').write_text('0x0')
    if fault=='ifindex':(node/'ifindex').write_text('9')
    if fault=='extra_supplicant':
        extra=proc/'21';extra.mkdir();(extra/'comm').write_text('wpa_supplicant');(extra/'cgroup').write_text('0::/other')
    if fault=='generation':
        old=node.with_name('old');node.rename(old);node.mkdir()
        for child in old.iterdir():
            if child.is_symlink():(node/child.name).symlink_to(child.resolve())
            else:(node/child.name).write_bytes(child.read_bytes())
    with pytest.raises(LeaseLost):verifier(lease,None)


def test_bad_handoff_latches_usb_relaunch_but_preserves_file(tmp_path,monkeypatch):
    g,s=guard_fixture();r,c=runtime(tmp_path,g)
    r._launch('wlan1');source=r.capture_file;source.write_bytes(b'synthetic')
    monkeypatch.setattr(r,'_handoff',lambda _:False)
    assert r._drop()
    assert r._lease_interlock and source.exists()
    assert r.tick()=='lease_blocked' and r.engine_attempts==1
    r.close()


def test_collector_receipt_requires_current_boot_and_root_regular_file(tmp_path,monkeypatch):
    import os
    import redux.radio.live_lease as mod
    target=tmp_path/'receipt';target.chmod(0o600) if target.exists() else None
    data={'sampled_monotonic':10.,'ethernet_verified':True,
          'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
          'radios':[{'iface':'wlan1','phy':'phy1','monitor':True,'mode':'monitor','arphrd':803,
                     'decoded_frames':7,'owner_verified':True,'ifindex':8,'transport':'usb','owner':'capture_exclusive'}]}
    target.write_text(json.dumps(data));target.chmod(0o600)
    original=os.fstat
    def root_stat(fd):
        fields=list(original(fd));fields[4]=0
        return os.stat_result(fields)
    monkeypatch.setattr(mod.os,'fstat',root_stat)
    assert FileLeaseEvidence(target)().radios[0].ifindex==8
    data['boot_id']='previous-boot';target.write_text(json.dumps(data))
    with pytest.raises(LeaseLost):FileLeaseEvidence(target)()
    fifo=tmp_path/'fifo';os.mkfifo(fifo)
    with pytest.raises(LeaseLost):FileLeaseEvidence(fifo)()


def test_disabled_usb_mode_uses_usb_through_guarded_monitor(tmp_path,monkeypatch):
    # Turnkey policy: with the field lease disabled (the default), a USB adapter
    # is a first-class capture radio and falls through to the SAME guarded
    # ensure_monitor path as the onboard radio — it is no longer refused. The
    # connected-uplink guard inside ensure_monitor is the real protection (tested
    # in test_live_runtime.py); here we prove the USB gate no longer blocks.
    import redux.core.live_runtime as mod
    config=LiveConfig(state_dir=tmp_path/'s',active_dir=tmp_path/'a',capture_dir=tmp_path/'i',enable_web=False)
    assert config.require_usb_lease is False
    monkeypatch.setattr(mod,'usb_attached',lambda _:True)
    reached={'ensure_monitor':False}
    class _Reached(Exception):pass
    def spy_ensure_monitor(iface,*a,**k):
        reached['ensure_monitor']=iface
        raise _Reached  # stop _launch here; we only assert the USB gate let us through
    monkeypatch.setattr(mod,'ensure_monitor',spy_ensure_monitor)
    r=LiveRuntime(config,executor=lambda *a,**k:None,spawn=lambda *a,**k:None)
    with pytest.raises(_Reached):r._launch('wlan1')   # NOT LeaseLost — USB is allowed now
    assert reached['ensure_monitor']=='wlan1'          # it went through the guarded monitor path
    assert r.process is None and r.engine_attempts==0
    r.close()


@pytest.mark.parametrize('age', [True,0,-1,4,float('inf'),float('nan')])
def test_receipt_age_policy_cannot_disable_freshness(age):
    with pytest.raises(ValueError):UsbLeaseGuard(lambda:None,lambda *a:None,max_age=age)


def test_guard_detects_clock_rollback_even_when_receipt_is_within_age():
    g,s=guard_fixture();g.acquire('wlan1');s['now']=12;g.check()
    s['now']=11
    with pytest.raises(LeaseLost):g.check()
    assert g.blocked


@pytest.mark.parametrize('record', [[],{'blocked':False,'identity':{'iface':'wlan1','phy':'phy1','ifindex':True}},
                                   {'blocked':False,'identity':{},'kernel_identity':['private',1]}])
def test_malformed_interlock_stays_alive_but_blocks_capture(tmp_path,record):
    state=tmp_path/'state';state.mkdir()
    (state/'usb-lease-interlock.json').write_text(json.dumps(record))
    g,_=guard_fixture();r,c=runtime(tmp_path,g)
    assert r.tick()=='lease_blocked' and r.process is None
    r.close()
