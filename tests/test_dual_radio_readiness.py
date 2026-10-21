"""Read-only synthetic configuration/process/NM fixtures; never contact hardware."""
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest

from redux.core.runtime_identity import configuration_checkpoint
from redux.radio import readiness as r
from redux.radio.local_evidence import Unavailable


@pytest.fixture
def host(tmp_path,monkeypatch):
    proc=tmp_path/'proc';proc.mkdir()
    (proc/'sys/kernel/random').mkdir(parents=True)
    (proc/'sys/kernel/random/boot_id').write_text('synthetic-boot')
    (proc/'stat').write_text('btime 1000\n')
    binary=tmp_path/'bin';binary.mkdir()
    for pid,name,args in ((20,'python3.11',['python3','-m','redux.core.live_runtime']),
                          (21,'bettercap',['bettercap','-iface','usbtest'])):
        p=proc/str(pid);p.mkdir();(p/'fd').mkdir()
        exe=binary/name;exe.write_text('synthetic');(p/'exe').symlink_to(exe)
        (p/'stat').write_text(f'{pid} ({name}) S '+'0 '*18+f'{pid} 0')
        (p/'status').write_text('Uid:\t0\t0\t0\t0\n')
        (p/'cmdline').write_bytes(('\0'.join(args)+'\0').encode())
        (p/'cgroup').write_text('0::/system.slice/redux-live.service')
    cfg=tmp_path/'live.toml';cfg.write_text('[live]\npreferred_iface=""\n');cfg.chmod(0o600)
    (proc/'20/cmdline').write_bytes(('python3\0-m\0redux.core.live_runtime\0--config\0'+str(cfg)+'\0').encode())
    live=tmp_path/'live.json'
    state=dict(state='running',iface='usbtest',pid=21,updated_utc=2000,handed_off=3,events_seen=50,failure_streak=0)
    live.write_text(json.dumps(state));live.chmod(0o600)
    original=os.fstat
    def rootstat(fd):
        values=list(original(fd));values[4]=0;return os.stat_result(values)
    monkeypatch.setattr(r.os,'fstat',rootstat)
    def run(args):
        assert r.Reader.allowed(args)
        return '20\n'
    def facts():return r.runtime_facts(run,proc=proc,live=live,config=cfg,wall=lambda:2001)
    return proc,cfg,live,state,facts


def test_disk_auto_and_actual_usb_owner_are_separate_from_loaded_pin(host):
    public,_=host[4]()
    assert public['disk_preference']=='auto'
    assert public['supervisor_identity_verified'] and public['bettercap_identity_verified']
    assert public['bettercap_argument_onboard'] is False
    assert public['checkpoint_iface_matches_argument'] is True
    assert public['loaded_wlan0_pin']=='UNKNOWN'
    assert 'usbtest'not in json.dumps(public)
    assert public['counters']['handed_off']==3


def test_loaded_checkpoint_attestation_is_tied_to_boot_and_pid_generation(host):
    proc,cfg,live,state,facts=host
    config=SimpleNamespace(preferred_iface='wlan0',require_usb_lease=False,allow_connected_capture=False)
    state['configuration_identity']=configuration_checkpoint(config,pid=20,proc_root=proc)
    live.write_text(json.dumps(state))
    public,_=facts()
    assert public['loaded_wlan0_pin']=='PASS' and public['disk_preference']=='auto'
    # Mismatched disk facts must not silently replace the self-reported loaded policy.
    assert public['loaded_policy']['preferred_wlan0'] is True
    for key,value in [('supervisor_pid',21),('start_ticks',999),('boot_id','old'),('version',True),('preferred_wlan0','yes')]:
        changed=dict(state['configuration_identity']);changed[key]=value
        assert r.loaded_policy(changed,(20,20,0,0),'synthetic-boot') is None


@pytest.mark.parametrize('fault',['stale','future','missing_pid','uid','cgroup','args','executable','permission','symlink'])
def test_identity_and_stale_checkpoint_fail_closed(host,fault):
    proc,cfg,live,state,facts=host
    if fault=='stale':state['updated_utc']=1900
    if fault=='future':state['updated_utc']=2002
    if fault=='missing_pid':state['pid']=99
    if fault=='uid':(proc/'21/status').write_text('Uid:\t1000\t1000\t1000\t1000\n')
    if fault=='cgroup':(proc/'21/cgroup').write_text('0::/foreign')
    if fault=='args':(proc/'21/cmdline').write_bytes(b'bettercap\0-iface\0bad/path\0')
    if fault=='executable':(proc/'21/exe').unlink()
    if fault=='permission':cfg.chmod(0o666)
    if fault=='symlink':
        real=cfg.with_name('real');cfg.rename(real);cfg.symlink_to(real)
    live.write_text(json.dumps(state))
    with pytest.raises((Unavailable,OSError,ValueError)):facts()


def test_no_child_keeps_verified_supervisor_and_disk_policy(host):
    proc,cfg,live,state,facts=host
    state.update(pid=None,state='degraded');live.write_text(json.dumps(state))
    public,private=facts()
    assert public['supervisor_identity_verified'] and not public['bettercap_identity_verified']
    assert private[1]is None and public['loaded_wlan0_pin']=='UNKNOWN'


class Nm:
    def __init__(self):self.owner=':1.20';self.name='usbtest';self.managed=True;self.auto=True;self.state=20;self.calls=[]
    def __call__(self,args):
        assert r.Reader.allowed(args);self.calls.append(tuple(args))
        if args[0]=='nmcli':return '/org/freedesktop/NetworkManager/Devices/3\n'
        if args[3]=='call':return 's '+json.dumps(self.owner)
        key=args[-1]
        values={'Interface':self.name,'Managed':self.managed,'Autoconnect':self.auto,'State':self.state}
        return ('s'if key=='Interface'else'u'if key=='State'else'b')+' '+json.dumps(values[key])


def test_nm_reads_actual_device_autoconnect_without_setting_anything():
    bus=Nm();facts=r.nm_policy(bus,'usbtest')
    assert facts==dict(managed=True,autoconnect=True,state=20,owner_stable=True)
    assert all('get-property'in c or 'GetNameOwner'in c or c[0]=='nmcli'for c in bus.calls)


@pytest.mark.parametrize('fault',['missing','owner','interface','bool','state'])
def test_nm_absence_replacement_and_invalid_types_never_mean_unmanaged(fault):
    bus=Nm();n=[0]
    def run(args):
        value=bus(args);n[0]+=1
        if fault=='missing'and args[0]=='nmcli':return ''
        if fault=='owner'and n[0]>3:bus.owner=':1.21'
        if fault=='interface':bus.name='other'
        if fault=='bool':bus.managed='no'
        if fault=='state':bus.state=True
        return value
    with pytest.raises(Unavailable):r.nm_policy(run,'usbtest')


@pytest.mark.parametrize('args',[['nexutil','-I','wlan0','-m2'],
    ['/usr/local/bin/nexutil','-I','wlan0','-m2'],['iw','dev','wlan0','set','type','monitor'],
    ['nmcli','device','set','usbtest','autoconnect','no'],['systemctl','restart','redux-live.service'],
    ['busctl','--system','--auto-start=no','set-property',':1.20','/x','x','Autoconnect','b','false']])
def test_readonly_command_barrier_refuses_every_mutation(args):
    assert not r.Reader.allowed(args)


def test_capture_inventory_never_reads_bytes_or_outputs_names(tmp_path):
    for name in ('active','incoming','ready'):(tmp_path/name).mkdir()
    (tmp_path/'active'/'PRIVATE-RF-NAME.pcap').write_bytes(b'NOT READ')
    value=r.capture_counts(tmp_path)
    assert value['active']['files']==1 and value['active']['bytes']==8
    assert 'PRIVATE'not in json.dumps(value)and'NOT READ'not in json.dumps(value)


def test_packet_inode_binding_reports_usb_and_rejects_pid_reuse(host):
    proc,*_=host
    (proc/'21/fd/3').symlink_to('socket:[222]')
    (proc/'net').mkdir();(proc/'net/packet').write_text('sk RefCnt Type Proto Iface R Rmem User Inode\n000 3 3 0003 9 1 0 0 222\n')
    identity=r.process_identity(21,proc)
    facts=r.packet_bindings(proc,identity,3,(9,))
    assert facts['usb_bound']==1 and facts['onboard_bound']==0 and facts['other_bound']==0
    (proc/'21/stat').write_text('21 (bettercap) S '+'0 '*18+'999 0')
    with pytest.raises(Unavailable):r.packet_bindings(proc,identity,3,(9,))


from test_awus036acm import linux  # shared synthetic sysfs/proc fixture


@pytest.fixture
def collector(linux):
    ops,state,sys,proc,usb,calls,live=linux
    (sys/'eth0/ifindex').write_text('2')
    original=ops._run
    def run(args):
        assert r.Reader.allowed(args)
        if args[:3]==['nmcli','-g','GENERAL.DBUS-PATH']:
            return '/org/freedesktop/NetworkManager/Devices/'+('3'if args[-1]=='wlan0'else'4')
        if args[0]=='busctl'and args[3]=='call':return 's ":1.20"'
        if args[0]=='busctl'and args[4]==':1.20':
            name='wlan0'if args[5].endswith('/3')else'usbrx'
            prop=args[-1]
            value={'Interface':name,'Managed':True,'Autoconnect':True,'State':20}[prop]
            return ('s'if prop=='Interface'else'u'if prop=='State'else'b')+' '+json.dumps(value)
        if args[0]=='/usr/local/bin/nexutil':return 'monitor: 0\n'
        if args[:5]==['ip','-j','addr','show','dev']and args[-1]=='usbrx':
            return '[{"ifname":"usbrx","ifindex":9,"flags":[],"addr_info":[]}]'
        return original(args)
    def observe(command=run):
        return r.collect_readiness('192.0.2.1',run=command,sys=sys,proc=proc,usb=usb.parent,
                                  live=live,config=ops.config_path,api=lambda:401)
    return observe,run,sys,proc,ops,live


def test_integrated_collector_is_stable_sanitized_and_never_qualifies_reception(collector):
    result=collector[0]()
    assert result['observation']=='stable' and result['ethernet_verified'] is True
    assert result['runtime']['loaded_wlan0_pin']=='UNKNOWN'
    assert result['runtime']['effective_onboard_assignment']=='PASS'
    assert result['alfa']['devices'][0]['autoconnect'] is True
    assert result['onboard']['arphrd']==1 and result['onboard']['firmware_monitor']==0
    assert result['onboard']['decoded_reception']=='UNKNOWN'
    assert '192.0.2'not in json.dumps(result) and 'usbrx'not in json.dumps(result)
    assert 'synthetic-boot'not in json.dumps(result)
    assert result['readiness']['state']=='BLOCKED'
    assert result['readiness']['decoded_reception']=='UNKNOWN'


@pytest.mark.parametrize('fault',['usb_gone','identity','pid','config','nm_owner','permission'])
def test_integrated_faults_discard_partial_positive_evidence(collector,fault):
    observe,run,sys,proc,ops,live=collector
    done=[False]
    def changed(args):
        value=run(args)
        if args[0]=='/usr/local/bin/nexutil'and not done[0]:
            done[0]=True
            if fault=='usb_gone':(sys/'usbrx/device').unlink()
            if fault=='identity':(sys/'usbrx/ifindex').write_text('10')
            if fault=='pid':(proc/'21/stat').write_text('21 (bettercap) S '+'0 '*18+'999 0')
            if fault=='config':ops.config_path.write_text('[live]\npreferred_iface=""\n')
            if fault=='permission':raise PermissionError('PRIVATE path')
        if fault=='nm_owner'and args[0]=='busctl'and args[3]=='call'and done[0]:
            # Both repeated owner values differ: fail the NM section, preserve
            # unknown policy rather than turn missing data into unmanaged.
            changed.count+=1
            return 's '+json.dumps(':1.'+str(100+changed.count))
        return value
    changed.count=0
    result=observe(changed)
    if fault in ('nm_owner','permission'):
        assert result['observation']=='stable'
        if fault=='nm_owner':assert result['alfa']['devices'][0]['managed'] is None
        else:assert result['onboard']['firmware_monitor'] is None
    else:
        assert result['observation']=='unverified'
        assert result['runtime']=={'loaded_wlan0_pin':'UNKNOWN'}
        assert result['alfa']['devices']==[]
    assert 'PRIVATE'not in json.dumps(result)


def test_root_receipt_missing_and_invalid_inputs_never_crash_checkpoint_helper(tmp_path):
    cfg=SimpleNamespace(preferred_iface='wlan0',require_usb_lease=False,allow_connected_capture=False)
    assert configuration_checkpoint(cfg,pid=10,proc_root=tmp_path)is None
    assert r.loaded_policy(None,(20,20,0,0),'boot')is None


def test_glass_box_readiness_can_only_authorize_a_later_qualification():
    value=dict(observation='stable',ethernet_verified=True,
        runtime=dict(loaded_wlan0_pin='PASS',effective_onboard_assignment='PASS',
                     bettercap_argument_onboard=True,onboard_bound=1,usb_bound=0,
                     wildcard_bound=0),
        onboard=dict(transport_onboard=True,native_framing=True,
                     decoded_reception='UNKNOWN'),
        alfa=dict(usb_candidates=1,netdev_count=1,devices=[dict(
            mode='monitor',arphrd=803,ip_addresses=0,owner_stable=True,
            managed=False,autoconnect=False)]))
    assessment=r.assess_readiness(value)
    assert assessment==dict(state='READY_FOR_SEPARATE_QUALIFICATION',blockers=[],
                            decoded_reception='UNKNOWN',physical_qualification='UNKNOWN')
    value['alfa']['devices'][0]['managed']=True
    blocked=r.assess_readiness(value)
    assert blocked['state']=='BLOCKED'
    assert blocked['blockers']==['alfa_exclusive_policy_unverified']


def test_dual_radio_acceptance_gate_affects_exit_contract(monkeypatch):
    from redux import acceptance
    from redux.radio import readiness
    def blocked(*_args,**_kwargs):
        return {'readiness':{'state':'BLOCKED','blockers':['capture_not_onboard']}}
    monkeypatch.setattr(readiness,'collect_readiness',blocked)
    result=acceptance.report('192.0.2.1',run=lambda _args:'',collector=lambda *_a,**_k:{},
                             dual_radio=True)
    assert result['gates']['dual_radio_readiness']=='FAIL'


def test_core_checkpoint_contains_loaded_fields_without_changing_selection(tmp_path):
    from redux.core.live_runtime import LiveRuntime
    from test_live_runtime import cfg
    runtime=LiveRuntime(cfg(tmp_path,preferred_iface='wlan0'),radio_probe=lambda:[])
    runtime.tick()
    payload=json.loads((runtime.config.state_dir/'live.json').read_text())
    assert payload['configuration_identity']['preferred_wlan0'] is True
    assert payload['configuration_identity']['auto_select'] is False
    assert payload['configuration_identity']['supervisor_pid']==os.getpid()
    assert 'configuration_identity'not in runtime._snapshot['runtime']
    runtime.close()


def test_explicit_onboard_selection_never_falls_back_to_ready_usb():
    from redux.core.live_runtime import choose_safe_radio
    from test_live_runtime import radio
    usb=radio('usbtest')
    def denied(*args,**kw):pytest.fail('must not even probe a replacement USB')
    assert choose_safe_radio([usb],preferred='wlan0',run=denied)is None


@pytest.mark.parametrize('path',['core/runtime_identity.py','radio/readiness.py'])
def test_assembled_image_requires_both_readonly_payloads(tmp_path,path):
    from test_root_payload import slot,gate
    root=slot(tmp_path)
    (root/'opt/redux/redux'/path).unlink()
    with pytest.raises(ValueError):gate.verify(root)


def test_memory_only_launcher_passes_opt_in_without_any_execution_flag(monkeypatch,capsys):
    from test_readonly_acceptance import launcher
    m=launcher();seen=[]
    def fetch(args,source):
        seen.append(args[-1])
        return dict(format='redux-readonly-acceptance-v1',hardware_changes=False,gates={'open':'UNKNOWN'})
    monkeypatch.setattr(m,'fetch',fetch)
    assert m.main(['--host','192.0.2.1','--dual-radio'])==2
    assert '--dual-radio'in seen[0]and'--execute-approved'not in seen[0]
    assert 'PRIVATE'not in capsys.readouterr().out


def test_entrypoint_opt_in_reaches_readonly_provider(monkeypatch,capsys):
    from redux import acceptance
    seen=[]
    def report(peer,**kw):
        seen.append(kw['dual_radio'])
        return {'gates':{'open':'UNKNOWN'}}
    monkeypatch.setattr(acceptance,'report',report)
    assert acceptance.main(['--management-peer','192.0.2.1','--dual-radio'])==2
    assert seen==[True]
