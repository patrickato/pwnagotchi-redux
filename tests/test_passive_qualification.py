"""Synthetic stage transitions and rollback; no hardware socket or command."""
from dataclasses import replace
import json
import pytest
from redux.radio.passive_qualification import Observation, Stop, run_once
from test_frame_headers import rt,frame


class Fake:
    def __init__(self,fail=None,partial=False):
        self.state=Observation(('synthetic-device',),('protected',),'managed',False,1,None,True,True)
        self.fail=fail;self.partial=partial;self.commands=[];self.consumed=False
        self.used=set();self.t=100.;self.socket=None;self.observations=0
    def clock(self):return self.t
    def deadline(self,end):self.end=end
    def receive_deadline(self,end):self.receive_end=end
    def observe(self):
        self.observations+=1
        if self.fail=='observe' and self.observations==3:raise Stop('private error')
        return self.state
    def pin_check(self,identity):
        if identity!=self.state.identity:raise Stop('private changed identity')
    def change(self,name):
        assert name not in self.commands
        self.commands.append(name)
        if self.fail==name and not self.partial:raise Stop('private error')
        if name=='monitor':self.state=replace(self.state,mode='monitor',arphrd=803)
        if name=='channel_2412':self.state=replace(self.state,frequency=2412)
        if name=='up':self.state=replace(self.state,up=True)
        if self.fail==name:raise Stop('private partial error')
    def open_receiver(self):
        if self.fail=='open':raise Stop('private open')
        self.socket=FakeReceiver(self)
        return self.socket
    def cleanup(self,baseline):
        assert not self.socket or self.socket.closed
        if self.state.identity!=baseline.identity or self.state.nm_unmanaged is not True:raise Stop('identity unknown')
        self.commands.append('cleanup')
        if self.fail=='cleanup':raise Stop('cleanup private')
        self.state=replace(self.state,mode=baseline.mode,up=baseline.up,arphrd=baseline.arphrd,frequency=baseline.frequency)


class FakeReceiver:
    def __init__(self,ops):self.ops=ops;self.closed=False
    def receive(self,timeout):
        self.ops.t+=max(.1,timeout)
        if self.ops.fail=='receive':raise OSError('PRIVATE MAC')
        if self.ops.fail=='replacement':self.ops.state=replace(self.ops.state,identity=('replacement',))
        if self.ops.fail=='ownership':self.ops.state=replace(self.ops.state,nm_unmanaged=False)
        if self.ops.fail=='runtime':self.ops.state=replace(self.ops.state,protected=('new-bettercap',))
        if self.ops.fail=='link':return rt(frame()),True,False,1
        if self.ops.fail=='outgoing':return rt(frame()),False,False,803
        if self.ops.fail=='truncated':return rt(frame()),True,True,803
        if self.ops.fail=='empty':return None
        if self.ops.fail=='late':self.ops.t+=61
        return rt(frame()),True,False,803
    def close(self):
        if self.ops.fail=='close':raise OSError('PRIVATE')
        self.closed=True


def test_unapproved_is_readonly_and_reports_unchanged_baseline():
    ops=Fake();r=run_once(ops,clock=ops.clock)
    assert r['before']==r['after']=='PASS' and r['restored'] is True
    assert not r['qualified'] and ops.commands==[] and ops.socket is None


def test_one_bounded_passive_attempt_restores_before_qualification():
    ops=Fake();r=run_once(ops,approved=True,seconds=1,clock=ops.clock)
    assert r['qualified'] and r['restored'] and r['counts']['management']>0
    assert ops.commands==['monitor','passive_flags','channel_2412','up','cleanup']
    assert ops.socket.closed
    again=run_once(ops,approved=True,clock=ops.clock)
    assert again['failure']=='attempt_consumed'
    assert 'synthetic-device' not in json.dumps(r)


@pytest.mark.parametrize('failure',['monitor','passive_flags','channel_2412','up','open','observe','receive'])
@pytest.mark.parametrize('partial',[False,True])
def test_partial_failures_close_before_single_cleanup(failure,partial):
    ops=Fake(failure,partial);r=run_once(ops,approved=True,seconds=1,clock=ops.clock)
    assert not r['qualified'] and r['restored'] is True
    assert ops.commands.count('cleanup')<=1
    assert 'private' not in json.dumps(r).lower()


@pytest.mark.parametrize('failure',['replacement','ownership','close','cleanup'])
def test_uncertain_identity_owner_or_cleanup_never_claims_restoration(failure):
    ops=Fake(failure);r=run_once(ops,approved=True,seconds=1,clock=ops.clock)
    assert not r['qualified'] and not r['restored']
    if failure=='replacement':assert 'cleanup' not in ops.commands
    if failure=='close':
        assert 'cleanup' not in ops.commands
        assert r['failure']=='socket_close_unverified'


@pytest.mark.parametrize('failure',['outgoing','truncated','empty','late'])
def test_no_decoded_frames_stays_unknown(failure):
    ops=Fake(failure);r=run_once(ops,approved=True,seconds=1,clock=ops.clock)
    assert r['restored'] and not r['qualified']
    assert r['test']==('FAIL' if failure=='late' else 'UNKNOWN')


@pytest.mark.parametrize('failure',['runtime','link'])
def test_runtime_rotation_or_wrong_link_stops_without_qualified_result(failure):
    ops=Fake(failure);r=run_once(ops,approved=True,seconds=2,clock=ops.clock)
    assert not r['qualified'] and r['failure'] is not None


@pytest.mark.parametrize('field,value',[('safe',False),('safe','yes'),('nm_unmanaged',None),
    ('mode','monitor'),('up',True),('arphrd',803),('frequency',2412)])
def test_baseline_unknown_or_already_active_refuses_mutation(field,value):
    ops=Fake();ops.state=replace(ops.state,**{field:value})
    r=run_once(ops,approved=True,clock=ops.clock)
    assert r['before']=='UNKNOWN' and not ops.commands and not r['qualified']


@pytest.mark.parametrize('seconds',[0,-1,60.01,float('nan'),float('inf'),True])
def test_invalid_deadlines_fail_before_any_backend(seconds):
    with pytest.raises(ValueError):run_once(object(),seconds=seconds)


def test_receive_alarm_closes_before_guard_and_cleanup():
    from redux.radio.passive_qualification import WindowExpired
    ops=Fake()
    original=ops.open_receiver
    def opened():
        r=original()
        r.receive=lambda timeout:(_ for _ in ()).throw(WindowExpired())
        return r
    ops.open_receiver=opened
    r=run_once(ops,approved=True,seconds=1,clock=ops.clock)
    assert ops.socket.closed and r['restored'] and r['test']=='UNKNOWN'


def test_clock_regression_never_qualifies_or_retries():
    ops=Fake();original=ops.open_receiver
    def opened():
        r=original()
        r.receive=lambda timeout:setattr(ops,'t',ops.t-1)
        return r
    ops.open_receiver=opened
    r=run_once(ops,approved=True,seconds=1,clock=ops.clock)
    assert not r['qualified'] and r['failure']=='clock_invalid'
    assert ops.socket.closed


def test_receiver_budget_cannot_run_unbounded():
    ops=Fake();original=ops.open_receiver
    def opened():
        r=original()
        # No time progression: hard packet/byte count must stop this source.
        r.receive=lambda timeout:(b'\0'*8192,True,False,803)
        return r
    ops.open_receiver=opened
    r=run_once(ops,approved=True,seconds=1,clock=ops.clock)
    assert r['failure']=='receive_budget' and r['restored'] and not r['qualified']


def test_gate_snapshots_are_predicates_and_actual_link_only():
    ops=Fake('runtime');r=run_once(ops,approved=True,seconds=2,clock=ops.clock)
    assert r['last_gate']['protected_preserved'] is False
    assert 'new-bettercap' not in json.dumps(r)


def test_partial_receiver_construction_with_failed_close_refuses_mutation():
    ops=Fake()
    class Partial:
        close_attempted=True
        def close(self):pytest.fail('must not retry failed close')
    def fail_open():
        ops.receiver=Partial()
        raise OSError('constructor close failed')
    ops.open_receiver=fail_open
    r=run_once(ops,approved=True,seconds=1,clock=ops.clock)
    assert r['failure']=='socket_close_unverified' and not r['restored']
    assert 'cleanup' not in ops.commands


def test_link_framing_change_without_another_packet_cannot_qualify():
    ops=Fake();original=ops.open_receiver
    def opened():
        r=original();receive=r.receive
        def changed(timeout):
            item=receive(timeout)
            ops.state=replace(ops.state,arphrd=1)
            return item
        r.receive=changed
        return r
    ops.open_receiver=opened
    r=run_once(ops,approved=True,seconds=1,clock=ops.clock)
    assert not r['qualified'] and r['last_gate']['link_expected'] is False
