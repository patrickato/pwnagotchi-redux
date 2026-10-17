"""Synthetic BlueZ properties: no bus, agent, pairing or networking."""
import json
import pytest
from redux.connectivity.pan_preflight import collect_pan, gates
from redux.radio.local_evidence import BoundedReader, Unavailable

PEER='/org/bluez/hci0/dev_02_00_00_00_00_01'


class Bus:
    def __init__(self):
        self.calls=[];self.owner=':1.20';self.change=None
        self.values={'Powered':True,'Pairable':False,'Discoverable':False,
            'Paired':True,'Trusted':True,'Bonded':True,'Adapter':'/org/bluez/hci0',
            'Address':'02:00:00:00:00:01','Connected':True,'Interface':'bnep0','UUID':'nap'}
    def __call__(self,args):
        assert BoundedReader.allowed(args)
        self.calls.append(tuple(args))
        if args[:4]==['ip','-j','route','get']:
            if self.change:self.change(self)
            return '[{"dev":"bnep0","prefsrc":"192.0.2.2"}]'
        if args[:3]==['ip','-j','addr']:
            return '[{"ifname":"bnep0","ifindex":8,"flags":["UP"],"addr_info":[{"local":"192.0.2.2"}]}]'
        if args[3]=='call':return 's '+json.dumps(self.owner)
        name=args[-1]
        value=self.values[name]
        return ('b' if type(value)is bool else 'o' if name=='Adapter' else 's')+' '+json.dumps(value)


def test_pan_gates_do_not_infer_independent_authentication():
    bus=Bus();facts=collect_pan(bus,PEER,'192.0.2.1')
    result=gates(facts)
    assert result['paired']==result['trusted']==result['bonded']=='PASS'
    assert result['network_connected']==result['ip_route_verified']=='PASS'
    assert result['authenticated_bond']==result['redux_session_authenticated']=='UNKNOWN'
    assert all('get-property' in c or 'GetNameOwner' in c or c[0]=='ip' for c in bus.calls)


@pytest.mark.parametrize('name',['Paired','Trusted','Bonded','Powered','Connected'])
def test_false_properties_stay_false(name):
    bus=Bus();bus.values[name]=False
    expected={'Paired':'paired','Trusted':'trusted','Bonded':'bonded','Powered':'adapter_closed','Connected':'network_connected'}[name]
    assert gates(collect_pan(bus,PEER,'192.0.2.1'))[expected]=='FAIL'


@pytest.mark.parametrize('name,value',[('Address','02:00:00:00:00:02'),('Adapter','/org/bluez/hci1'),
    ('Interface','eth0'),('UUID','unexpected'),('Pairable',True),('Discoverable',True)])
def test_identity_and_closed_adapter_fail(name,value):
    bus=Bus();bus.values[name]=value
    results=gates(collect_pan(bus,PEER,'192.0.2.1'))
    assert 'FAIL' in results.values()
    assert results['authenticated_bond']=='UNKNOWN'


@pytest.mark.parametrize('change',['owner','peer','disconnect'])
def test_changed_snapshot_never_promotes_to_ready(change):
    bus=Bus()
    def mutate(b):
        if change=='owner':b.owner=':1.21'
        if change=='peer':b.values['Address']='02:00:00:00:00:02'
        if change=='disconnect':b.values['Connected']=False
    bus.change=mutate
    with pytest.raises(Unavailable,match='changed'):collect_pan(bus,PEER,'192.0.2.1')


def test_missing_network_interface_is_unknown_not_connected():
    bus=Bus()
    def missing(args):
        if 'org.bluez.Network1' in args:raise Unavailable('command_failed')
        return bus(args)
    result=gates(collect_pan(missing,PEER,'192.0.2.1'))
    assert result['network_connected']==result['ip_route_verified']=='UNKNOWN'
    assert result['paired']=='PASS'


@pytest.mark.parametrize('value',[1,'yes',{},[],None])
def test_truthiness_never_satisfies_security_gate(value):
    assert gates({'authenticated_bond':value})['authenticated_bond']=='UNKNOWN'
