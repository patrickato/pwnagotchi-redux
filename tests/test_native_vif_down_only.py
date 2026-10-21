"""DOWN-only runtime capability and AST checks; no actual hardware calls."""
import ast
import inspect
import textwrap

import pytest

from test_native_vif_isolation import Fake, NAME, trial


class DownFake(Fake):
    down_only=True
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.s['temp_up']=False
        self.observed=0
    def observe_down(self,name,index,phy):
        assert (name,index,phy)==(NAME,9,'phy0')
        self.observed+=1
        if self.fail=='observation':raise trial.Stop('bounded observation failed')
        return [{'ifindex':9,'arphrd':803,'firmware':0,'up':False}]
    def sample(self,*a,**kw):pytest.fail('DOWN-only must never sample packets')


def test_down_only_unknown_nm_is_observed_without_rf_permission():
    class Unknown(DownFake):
        def change(self,args):
            super().change(args)
            if args[:2]==['iw','phy']:self.s['temp_nm_managed']=None
    ops=Unknown()
    result=trial.run_trial(ops,NAME,approved=True,down_only=True)
    assert result['restored'] is True and result['test']['down_only'] is True
    assert result['post_create']['failed_checks']==['temporary_nm_unmanaged']
    assert ops.observed==1 and trial.trial_exit_code(result,execution=True)==0
    assert ['ip','link','set','dev',NAME,'up'] not in ops.commands
    assert 'reception' not in result['test']
    assert ['ip','link','set','dev','wlan0','up'] in ops.commands  # primary rollback only


@pytest.mark.parametrize('field,value', [('temp_up',True),('temp_monitor',False),('temp_phy','phy9'),
                                        ('temp_type',1),('firmware',2),('temp_nm_managed',True)])
def test_unexpected_down_only_state_never_enters_observation(field,value):
    class Bad(DownFake):
        def change(self,args):
            super().change(args)
            if args[:2]==['iw','phy']:self.s[field]=value
    ops=Bad();result=trial.run_trial(ops,NAME,approved=True,down_only=True)
    assert 'stopped' in result['test'] and ops.observed==0
    assert ['ip','link','set','dev',NAME,'up'] not in ops.commands
    if field in ('temp_monitor','temp_phy'):
        assert result['restored'] is False
        assert ['iw','dev',NAME,'del'] not in ops.commands


@pytest.mark.parametrize('failure', ['stop','managed_no','create','observation','down','delete',
                                    'primary_monitor','primary_up','managed_yes','start'])
@pytest.mark.parametrize('partial', [False,True])
def test_down_only_errors_use_one_narrow_cleanup_without_activation(failure,partial):
    ops=DownFake(failure,partial)
    result=trial.run_trial(ops,NAME,approved=True,down_only=True)
    assert len(ops.commands)==len({tuple(cmd) for cmd in ops.commands})
    assert ['ip','link','set','dev',NAME,'up'] not in ops.commands
    assert not any(cmd[0] in ('modprobe','reboot') or 'restart' in cmd for cmd in ops.commands)
    expected = (failure not in ('delete','primary_monitor','primary_up','managed_yes','start')
                or partial and failure in ('down','delete'))
    if failure == 'create' and partial:
        expected = False  # no conclusively recorded ifindex after partial create
        assert ['iw','dev',NAME,'del'] not in ops.commands
    assert result['restored'] is expected


def test_down_only_requires_capability_backend_and_approval():
    with pytest.raises(trial.Stop,match='backend'):
        trial.run_trial(Fake(),NAME,approved=True,down_only=True)
    ops=DownFake()
    with pytest.raises(trial.Stop,match='approval'):
        trial.run_trial(ops,NAME,down_only=True)
    assert ops.commands==[]


@pytest.mark.parametrize('cleanup', [False,True])
@pytest.mark.parametrize('command', [
    ['ip','link','set','dev',NAME,'up'],['modprobe','brcmfmac'],['reboot'],
    ['systemctl','restart','NetworkManager'],['nexutil','-m2'],['iw','dev','eth0','del'],
])
def test_capability_backend_refuses_dangerous_commands_before_execution(monkeypatch,cleanup,command):
    ops=object.__new__(trial.DownOnlyPiOps);ops.cleaning=cleanup;ops.attempted=set()
    monkeypatch.setattr(trial.PiOps,'command',lambda *a,**kw:pytest.fail('must not reach backend'))
    with pytest.raises(trial.Stop,match='outside one-attempt'):ops.change(command)


def test_capability_backend_refuses_repeated_and_out_of_phase_mutations(monkeypatch):
    ops=object.__new__(trial.DownOnlyPiOps);ops.cleaning=False;ops.attempted=set()
    calls=[];monkeypatch.setattr(trial.PiOps,'command',lambda self,args:calls.append(args))
    stop=['systemctl','stop','redux-live.service']
    ops.change(stop)
    with pytest.raises(trial.Stop):ops.change(stop)
    with pytest.raises(trial.Stop):ops.change(['ip','link','set','dev','wlan0','up'])
    ops.cleaning=True
    with pytest.raises(trial.Stop):ops.change(stop)
    assert calls==[stop]


def test_static_profile_has_no_packet_socket_or_sample_call():
    from test_native_vif_isolation import path
    full=ast.parse(path.read_text())
    node=next(n for n in full.body if isinstance(n,ast.ClassDef) and n.name=='DownOnlyPiOps')
    tree=ast.Module(body=[node],type_ignores=[])
    for node in ast.walk(tree):
        if isinstance(node,ast.Call):
            if isinstance(node.func,ast.Attribute):
                assert node.func.attr not in ('socket','recvfrom','sample','send','sendto')
    sample=next(n for n in tree.body[0].body if isinstance(n,ast.FunctionDef) and n.name=='sample')
    assert len(sample.body)==1 and isinstance(sample.body[0],ast.Raise)
    with pytest.raises(trial.Stop,match='forbidden'):
        object.__new__(trial.DownOnlyPiOps).sample()


def test_real_down_observer_filters_udev_and_dbus_identifiers(monkeypatch):
    fake=DownFake();fake.change(['systemctl','stop','redux-live.service'])
    fake.change(['nmcli','--wait','5','device','set','wlan0','managed','no'])
    fake.change(['iw','phy','phy0','interface','add',NAME,'type','monitor'])
    ops=object.__new__(trial.DownOnlyPiOps)
    ops.deadline=trial.time.monotonic()+100
    ops.observe=fake.observe
    events=[];ops.event=lambda phase,**data:events.append(data)
    def command(args,**kw):
        if 'GetNameOwner' in args:return 0,'s ":1.20"\n'
        if 'GetDevices' in args or 'GetAllDevices' in args:return 0,'ao 0\n'
        if args[0]=='udevadm':return 0,'NM_UNMANAGED=1\nID_NET_MANAGED_BY=PRIVATE-OWNER\nSSID=PRIVATE-NETWORK\n'
        pytest.fail('unexpected command')
    ops.command=command
    monkeypatch.setattr(trial.time,'sleep',lambda _:None)
    result=ops.observe_down(NAME,9,'phy0')
    assert len(result)==2 and 'PRIVATE' not in repr(result)
    assert result[0]['udev']['NM_UNMANAGED']=='true'
    assert result[0]['inventories']['GetAllDevices']['matches']==[]
    assert result[0]['nm_owner_stable'] is True
    assert ops.command_limit==6


@pytest.mark.parametrize('command', [
    ['ip','link','set','dev',NAME,'up'], ['nexutil','-I',NAME,'-m2'],
    ['iw','dev',NAME,'set','channel','1'], ['modprobe','brcmfmac'],
    ['udevadm','trigger'], ['systemctl','restart','NetworkManager'],
])
def test_direct_command_cannot_bypass_down_only_mutation_allowlist(monkeypatch,command):
    ops=object.__new__(trial.DownOnlyPiOps);ops.peer='192.0.2.1'
    monkeypatch.setattr(trial.PiOps,'command',lambda *a,**kw:pytest.fail('forbidden backend reached'))
    with pytest.raises(trial.Stop,match='forbidden'):ops.command(command)
