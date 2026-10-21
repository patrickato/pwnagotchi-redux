"""Linux backend fixtures and syscall doubles only; no Pi/RF/socket creation."""
import ast
from dataclasses import replace
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from redux.radio import awus036acm as a
from redux.radio.passive_qualification import Observation, Stop


@pytest.fixture
def linux(tmp_path,monkeypatch):
    sys=tmp_path/'net';sys.mkdir();proc=tmp_path/'proc';proc.mkdir()
    bus=tmp_path/'bus';(bus/'usb').mkdir(parents=True);(bus/'sdio').mkdir()
    drivers=tmp_path/'drivers'
    for name in ('mt76x2u','brcmfmac'):(drivers/name).mkdir(parents=True)
    hub=tmp_path/'devices/usb1';hub.mkdir(parents=True)
    (hub/'idVendor').write_text('1d6b');(hub/'idProduct').write_text('0002')
    usb=hub/'1-1';usb.mkdir()
    for name,value in [('idVendor','0e8d'),('idProduct','7612'),('busnum','1'),('devnum','2'),('bcdDevice','0100')]:
        (usb/name).write_text(value)
    state={'mode':'managed','up':False,'freq':None,'nm':'no\n10 (unmanaged)\n'}
    for iface,index,phy,transport,driver,device in (
        ('usbrx',9,'phy1','usb','mt76x2u',usb/'1-1:1.0'),
        ('wlan0',3,'phy0','sdio','brcmfmac',tmp_path/'sdio-device')):
        device.mkdir();(device/'subsystem').symlink_to(bus/transport)
        (device/'driver').symlink_to(drivers/driver)
        p=sys/iface;p.mkdir();(p/'device').symlink_to(device)
        ph=tmp_path/phy;ph.mkdir();(p/'phy80211').symlink_to(ph)
        for key,value in [('ifindex',str(index)),('type','1'),('flags','0x1' if iface=='wlan0' else '0x0')]:
            (p/key).write_text(value)
    (sys/'eth0').mkdir()
    (proc/'sys/kernel/random').mkdir(parents=True);(proc/'sys/kernel/random/boot_id').write_text('synthetic-boot')
    (proc/'device-tree').mkdir();(proc/'device-tree/model').write_bytes(b'Raspberry Pi 4 Model B\0')
    (proc/'net').mkdir();(proc/'net/packet').write_text('sk RefCnt Type Proto Iface R Rmem User Inode\n000 3 3 0003 3 1 0 0 111\n')
    binaries=tmp_path/'bin';binaries.mkdir()
    for pid,name,cgroup in [(20,'wpa_supplicant','wpa_supplicant.service'),(21,'bettercap','redux-live.service'),(22,'python3','redux-live.service')]:
        p=proc/str(pid);p.mkdir();(p/'fd').mkdir()
        binary=binaries/name;binary.write_text('synthetic executable');(p/'exe').symlink_to(binary)
        (p/'comm').write_text(name)
        (p/'cgroup').write_text('0::/system.slice/'+cgroup)
        (p/'stat').write_text(f'{pid} ({name}) S '+'0 '*18+f'{pid} 0')
        (p/'status').write_text('Uid:\t0\t0\t0\t0\n')
        (p/'cmdline').write_bytes(b'bettercap\0-iface\0wlan0\0')
    (proc/'21/fd/5').symlink_to('socket:[111]')
    live=tmp_path/'live.json';live.write_text(json.dumps(dict(state='running',iface='wlan0',pid=21,updated_utc=time.time())))
    original=os.fstat
    def rootstat(fd):
        fields=list(original(fd));fields[4]=0;return os.stat_result(fields)
    monkeypatch.setattr(a.os,'fstat',rootstat)
    monkeypatch.setattr(a.platform,'machine',lambda:'aarch64')
    config=tmp_path/'live.toml';config.write_text('[live]\npreferred_iface="wlan0"\n');config.chmod(0o600);os.utime(config,(1,1))
    (proc/'22/cmdline').write_bytes(('python3\0-m\0redux.core.live_runtime\0--config\0'+str(config)+'\0').encode())
    (proc/'stat').write_text('btime 1000\n')
    ops=a.AdapterOps('usbrx','192.0.2.1',execution_approved=True,sys_root=sys,proc_root=proc,live_path=live,config_path=config)
    calls=[]
    def run(args,**kw):
        calls.append(args)
        if args==['iw','phy']:
            return 'Wiphy phy1\n\tSupported interface modes:\n\t\t * managed\n\t\t * monitor\n'
        if args==['iw','dev']:
            return f'phy#0\n Interface wlan0\n  ifindex 3\n  type monitor\nphy#1\n Interface usbrx\n  ifindex 9\n  type {state["mode"]}\n'
        if args[:2]==['iw','dev']:
            iface=args[2]
            if args[-1]=='info':
                return ('type monitor\nwiphy 0\nchannel 11 (2462 MHz)\n' if iface=='wlan0' else
                    f'type {state["mode"]}\nwiphy 1\n'+(f'channel 1 ({state["freq"]} MHz)\n' if state['freq'] else ''))
            if 'set' in args:
                if args[4]=='type':
                    state['mode']=args[5];(sys/'usbrx/type').write_text('803' if args[5]=='monitor' else '1')
                    if args[5]=='managed':state['freq']=None
                if args[4]=='freq':state['freq']=int(args[5])
                return ''
        if args[:2]==['ip','link']:
            state['up']=args[-1]=='up';(sys/'usbrx/flags').write_text('0x1' if state['up'] else '0x0');return ''
        if args[:4]==['ip','-j','route','get']:return '[{"dev":"eth0","prefsrc":"192.0.2.2"}]'
        if args[:3]==['ip','-j','addr']:return '[{"ifname":"eth0","ifindex":2,"flags":["UP"],"addr_info":[{"local":"192.0.2.2"}]}]'
        if args[0]=='nmcli':return 'yes\n20 (unavailable)\n' if args[-1]=='wlan0' else state['nm']
        if args[0]=='busctl':return 'ao 0\n'
        if args==['iw','reg','get']:return 'global\ncountry US: DFS-FCC\n'
        if args[0]=='systemctl':return '22\n' if 'MainPID' in args[2] else 'active\n'
        pytest.fail('unplanned synthetic command')
    monkeypatch.setattr(ops,'_run',run)
    return ops,state,sys,proc,usb,calls,live


def test_linux_measures_exact_usb_ancestry_and_onboard_owner(linux):
    ops,*_=linux
    observation=ops.observe()
    assert observation.safe is True and observation.nm_unmanaged is True
    assert observation.mode=='managed' and observation.up is False
    assert observation==ops.observe()
    assert '0e8d' not in repr(observation) and 'synthetic-boot' not in repr(observation)


def test_linux_exact_commands_and_identity_pinned_rollback(linux):
    ops,state,sys,proc,usb,calls,live=linux
    baseline=ops.observe();ops.consumed=True
    for command in ('monitor','passive_flags','channel_2412','up'):ops.change(command)
    active=ops.observe();assert active.mode=='monitor' and active.up
    ops.cleanup(baseline)
    assert ops.observe()==baseline
    changes=[cmd for cmd in calls if 'set' in cmd]
    assert all('usbrx'in cmd and 'wlan0'not in cmd and 'eth0'not in cmd for cmd in changes)
    assert not any(cmd[0]=='nmcli' and 'set'in cmd or 'restart'in cmd for cmd in calls)


@pytest.mark.parametrize('fault',['model','driver','usb','ifindex','nm','packet','wildcard','pid','supplicant','live_stale','live_iface','uid'])
def test_linux_negative_identity_owner_and_runtime_gates(linux,fault):
    ops,state,sys,proc,usb,calls,live=linux
    ops.observe()
    if fault=='model':(usb/'idProduct').write_text('7632')
    if fault=='driver':(sys/'usbrx/device/driver').unlink()
    if fault=='usb':(sys/'usbrx/device').unlink()
    if fault=='ifindex':(sys/'usbrx/ifindex').write_text('10')
    if fault=='nm':state['nm']=''
    if fault in ('packet','wildcard'):
        with (proc/'net/packet').open('a') as f:f.write(f'000 3 3 0003 {9 if fault=="packet" else 0} 1 0 0 999\n')
    if fault=='pid':(proc/'21/stat').write_text('21 (bettercap) S '+'0 '*18+'999 0')
    if fault=='supplicant':(proc/'20/cgroup').write_text('0::/foreign')
    if fault.startswith('live_'):
        d=json.loads(live.read_text());d['updated_utc'if fault=='live_stale' else 'iface']=0 if fault=='live_stale'else'usbrx';live.write_text(json.dumps(d))
    if fault=='uid':(proc/'21/status').write_text('Uid:\t1000\t1000\t1000\t1000\n')
    if fault=='pid':
        assert ops.observe().protected!=ops.baseline_protected
    else:
        with pytest.raises((Stop,OSError)):ops.observe()
    assert not any('set'in cmd for cmd in calls)


def test_identity_loss_refuses_cleanup_commands(linux):
    ops,state,sys,proc,usb,calls,live=linux
    baseline=ops.observe();ops.consumed=True;ops.change('monitor')
    (usb/'devnum').write_text('3')
    count=len(calls)
    with pytest.raises(Stop):ops.cleanup(baseline)
    assert not any('set'in cmd for cmd in calls[count:])


def test_readonly_backend_and_phase_barriers_refuse_direct_bypass(linux):
    ops,*_=linux
    ops.observe()
    with pytest.raises(Stop):ops.change('up')
    with pytest.raises(Stop):ops.change('create_vif')
    with pytest.raises(Stop):ops.open_receiver()
    ops.execution_approved=False;ops.consumed=True
    with pytest.raises(Stop):ops.change('monitor')


def test_receiver_protocol_zero_bind_and_no_transmit(monkeypatch):
    calls=[]
    class Sock:
        def fileno(self):return 44
        def setsockopt(self,*args):calls.append(('option',args))
        def bind(self,args):calls.append(('bind',args))
        def getsockname(self):return ('usbrx',3,0,803,b'')
        def close(self):self.closed=True
    s=Sock();s.closed=False
    original_fileno=s.fileno
    s.fileno=lambda:-1 if s.closed else original_fileno()
    monkeypatch.setattr(a.socket,'socket',lambda *args:(calls.append(('socket',args)) or s))
    monkeypatch.setattr(a.os,'fstat',lambda fd:SimpleNamespace(st_ino=99))
    ops=SimpleNamespace(iface='usbrx',pinned=('identity',),pin_check=lambda i:calls.append(('pin',i)),receiver=None)
    receiver=a.Receiver(ops)
    assert calls[0]==('socket',(a.socket.AF_PACKET,a.socket.SOCK_RAW,0))
    assert ('bind',('usbrx',3))in calls and ('option',(263,23,1))in calls
    receiver.close();assert s.closed and ops.receiver is None


def test_static_backend_has_no_transmit_service_nm_or_vif_mutations():
    source=Path('redux/radio/awus036acm.py').read_text();tree=ast.parse(source)
    calls={n.func.attr for n in ast.walk(tree)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)}
    assert not calls&{'send','sendto','sendmsg','connect','listen','accept','publish','acquire','rearm'}
    assert 'modprobe'not in source and 'reboot'not in source
    assert "'nmcli','device','set'"not in source and "'systemctl','restart'"not in source


def test_unpinned_runtime_cannot_be_taken_over_at_next_rotation(linux):
    ops,*_=linux
    ops.config_path.write_text('[live]\npreferred_iface=""\n')
    with pytest.raises(Stop,match='runtime_not_pinned'):ops.observe()


def test_changed_config_after_supervisor_start_is_not_assumed_loaded(linux):
    ops,*_=linux
    os.utime(ops.config_path,None)
    with pytest.raises(Stop,match='runtime_not_pinned'):ops.observe()


def test_packet_socket_bound_wrong_index_is_not_accepted_by_name(linux):
    ops,state,sys,proc,usb,calls,live=linux
    ops.observe()
    ops.receiver=SimpleNamespace(inode=999)
    with (proc/'net/packet').open('a')as f:f.write('000 3 3 0003 10 1 0 0 999\n')
    with pytest.raises(Stop,match='socket_binding'):ops.observe()


def test_raw_command_and_mutator_cannot_bypass_pinned_target(linux):
    ops,*_=linux
    ops.observe();ops.consumed=True
    # Restore the real bounded method, not the synthetic command supplier.
    for args in (['ip','link','set','dev','wlan0','down'],['modprobe','-r','mt76x2u'],
                 ['iw','dev','usbrx','set','freq','2462'],['systemctl','restart','redux-live.service']):
        with pytest.raises(Stop,match='command_denied'):
            a.AdapterOps._run(ops,args,changing=True)
        with pytest.raises(Stop,match='mutation_denied'):
            ops._mutate('monitor',args)


def test_readonly_remaining_snapshot_reports_uncertainty_without_cleanup(linux):
    ops,state,sys,proc,usb,calls,live=linux
    baseline=ops.observe();ops.consumed=True;ops.change('monitor')
    state['nm']='yes\n20 (unavailable)\n'
    before=len(calls)
    remaining=ops.remaining()
    assert remaining==dict(identity_verified=True,adapter_mode='monitor',up=False,arphrd=803,nm_unmanaged=False)
    assert not any('set'in cmd for cmd in calls[before:])
    (usb/'devnum').write_text('9')
    assert ops.remaining()['identity_verified'] is False


@pytest.mark.parametrize('payload',['frame_headers.py','passive_qualification.py','awus036acm.py'])
def test_image_contract_requires_actual_qualification_payload(tmp_path,payload):
    from test_root_payload import slot,gate
    root=slot(tmp_path)
    (root/'opt/redux/redux/radio'/payload).unlink()
    with pytest.raises(ValueError):gate.verify(root)


def test_default_cli_is_readonly_without_packet_socket(monkeypatch,capsys):
    from test_passive_qualification import Fake
    ops=Fake();seen=[]
    monkeypatch.setattr(a.os,'geteuid',lambda:0)
    monkeypatch.setattr(a,'AdapterOps',lambda *args,**kw:(seen.append(kw)or ops))
    class Lock:
        def bind(self,address):pass
        def close(self):pass
    def socket(family,kind):
        assert family==a.socket.AF_UNIX
        return Lock()
    monkeypatch.setattr(a.socket,'socket',socket)
    handlers=[]
    def install(signum,handler):
        if callable(handler):handlers.append(handler)
        return None
    monkeypatch.setattr(a.signal,'signal',install)
    monkeypatch.setattr(a.signal,'setitimer',lambda *args:None)
    assert a.main(['--iface','usbrx','--management-peer','192.0.2.1'])==2
    result=json.loads(capsys.readouterr().out)
    assert result['before']=='PASS' and result['test']=='NOT_RUN'
    assert seen==[{'execution_approved':False}] and not ops.commands and ops.socket is None
    ops.window_end=time.monotonic()-1
    with pytest.raises(Stop)as error:handlers[0](a.signal.SIGTERM,None)
    assert not isinstance(error.value,a.WindowExpired)
    with pytest.raises(a.WindowExpired):handlers[0](a.signal.SIGALRM,None)


def test_cleanup_backend_itself_refuses_open_receive_socket(linux):
    ops,*_=linux
    ops.observe();ops.consumed=True;ops.change('monitor')
    ops.receiver=SimpleNamespace(inode=999)
    with pytest.raises(Stop,match='cleanup_socket_open'):
        ops._mutate('managed',['iw','dev','usbrx','set','type','managed'])
