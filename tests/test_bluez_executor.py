"""No bus/controller access: asynchronous BlueZ executor fault injection."""
import asyncio
from dataclasses import replace
import json

import pytest

from redux.connectivity.bluez_executor import (
    BluezPanExecutor, PanConfig, PanStopped, PairingAgent, run_executor, main,
)
from redux.connectivity.bluez_pan import NAP

PEER = '/org/bluez/hci0/dev_00_11_22_33_44_55'


class Bus:
    def __init__(self):
        self.name = ':1.7'
        self.calls = []
        self.closed = False
        self.agent = None
        self.generation = 0
        self.fail = None
        self.partial = False
        self.adapter = dict(Powered=True, Pairable=False, Discoverable=False,
                            PairableTimeout=0, DiscoverableTimeout=180)
        self.peer = dict(Adapter='/org/bluez/hci0', Address='00:11:22:33:44:55',
                         Paired=True, Bonded=True, Trusted=True, UUIDs=[NAP])
        self.network = dict(Connected=False, Interface='', UUID=NAP)

    async def owner(self): return self.name
    def epoch(self, path): return self.generation
    async def export_agent(self, agent): self.agent = agent
    async def close(self): self.closed = True

    async def call(self, owner, path, interface, member, signature, body):
        self.calls.append((path, interface, member, body))
        if self.fail == member and not self.partial:
            raise RuntimeError('private backend error')
        if member == 'GetAll':
            data = self.adapter if body == ['org.bluez.Adapter1'] else self.peer if body == ['org.bluez.Device1'] else self.network
            return dict(data)
        if member == 'Set': self.adapter[body[1]] = body[2]
        if member == 'Connect':
            self.network.update(Connected=True, Interface='bnep0')
        if member == 'Disconnect': self.network.update(Connected=False, Interface='')
        if member == 'Pair':
            await self.agent.confirmation(owner, path, 123456)
        if self.fail == member:
            raise RuntimeError('private partial failure')
        return 'bnep0' if member == 'Connect' else None


def setup(**changes):
    bus, now = Bus(), [1.0]
    config = PanConfig(enabled=True, peer=PEER, management_probe_peer='192.0.2.1', **changes)
    async def yes(key): return True
    async def route(iface): return iface == 'bnep0'
    executor = BluezPanExecutor(config, bus, yes, route, clock=lambda: now[0])
    return executor, bus, now


def run(coroutine): return asyncio.run(coroutine)


def test_disabled_does_not_open_or_access_bus(tmp_path, capsys):
    async def scenario():
        e, b, _ = setup()
        e.config = PanConfig()
        assert await e.start() is False
        await e.tick()
        assert b.calls == [] and b.agent is None
    run(scenario())
    config = tmp_path/'live.toml'
    config.write_text('[phone]\nenabled=false\n')
    assert main(['--config', str(config), '--pairing']) == 0
    assert json.loads(capsys.readouterr().out)['bus_opened'] is False


def test_outgoing_numeric_pairing_pan_and_verified_rollback():
    async def scenario():
        e, b, now = setup()
        assert await e.start()
        assert e.session.status(now=now[0])['discoverable'] is False
        await e.open_pairing(owner_authorized=True)
        await e._pair_task
        assert e.connected_iface == 'bnep0'
        assert e.session.status(now=now[0])['phase'] == 'connected'
        assert not any(c[2] == 'RequestDefaultAgent' for c in b.calls)
        assert not any(c[2] == 'Set' and c[3][1] in ('Trusted','Pairable','Discoverable','Powered') for c in b.calls)
        assert '00_11' not in repr(e.status())
        await e.stop()
        assert e.restored is True and b.closed
        assert b.adapter['PairableTimeout'] == 0 and b.adapter['DiscoverableTimeout'] == 180
        assert b.network['Connected'] is False
    run(scenario())


@pytest.mark.parametrize('field,value', [('Trusted',False), ('Bonded',False), ('Paired',False),
                                        ('Address','66:77:88:99:AA:BB'), ('Adapter','/org/bluez/hci1')])
def test_revoked_or_changed_peer_cannot_connect(field, value):
    async def scenario():
        e,b,_ = setup()
        await e.start()
        b.peer[field] = value
        e.agent.authenticated = True
        with pytest.raises(PanStopped): await e._connect()
        assert not any(c[2] == 'Connect' for c in b.calls)
        await e.stop()
    run(scenario())


@pytest.mark.parametrize('field', ['Pairable','Discoverable'])
def test_preexisting_open_adapter_refuses_mutation(field):
    async def scenario():
        e,b,_ = setup()
        b.adapter[field] = True
        with pytest.raises(PanStopped): await e.start()
        assert not any(c[2] == 'Set' for c in b.calls)
    run(scenario())


@pytest.mark.parametrize('fault', ['owner','generation','trust','route'])
def test_connected_fault_disconnects_own_pan_and_interlocks(fault):
    async def scenario():
        e,b,_ = setup()
        await e.start()
        await e.open_pairing(owner_authorized=True)
        await e._pair_task
        if fault == 'owner': b.name = ':1.8'
        if fault == 'generation': b.generation += 1
        if fault == 'trust': b.peer['Trusted'] = False
        if fault == 'route':
            async def no(_): return False
            e.route_verified = no
        count=len(b.calls)
        await e.tick()
        assert e.blocked and not e.started and not e.agent.authenticated
        if fault == 'owner':
            assert len(b.calls) == count  # never mutate replacement daemon
            assert e.restored is False
        elif fault == 'generation':
            assert not any(c[2]=='Disconnect' for c in b.calls[count:])
            assert e.restored is False
        else:
            assert b.network['Connected'] is False
        with pytest.raises(PanStopped): await e.start()
    run(scenario())


def test_trusted_reconnect_has_backoff_without_pairing():
    async def scenario():
        e,b,now = setup()
        await e.start()
        await e.open_pairing(owner_authorized=True)
        await e._pair_task
        b.network['Connected'] = False
        now[0] = 2
        await e.tick()
        assert e.session.status(now=2)['phase'] == 'backoff'
        baseline=sum(c[2]=='Pair' for c in b.calls)
        now[0]=7
        await e.tick()
        assert e.connected_iface == 'bnep0'
        assert sum(c[2]=='Pair' for c in b.calls)==baseline
        await e.stop()
    run(scenario())


def test_confirmation_rejects_sender_peer_expiry_and_late_user_answer():
    async def scenario():
        e,b,now = setup()
        a=e.agent
        a.owner=b.name
        a.until=10
        for sender,peer in [(':1.99',PEER),(b.name,PEER+'0')]:
            with pytest.raises(PanStopped): await a.confirmation(sender,peer,123456)
        async def late(_): now[0]=10; return True
        a.confirm=late
        with pytest.raises(PanStopped): await a.confirmation(b.name,PEER,123456)
        assert not a.authenticated
    run(scenario())


@pytest.mark.parametrize('member', ['RegisterAgent','Set','Connect'])
@pytest.mark.parametrize('partial', [False,True])
def test_partial_errors_release_only_owned_resources(member, partial):
    async def scenario():
        e,b,_ = setup()
        if member=='RegisterAgent':
            b.fail=member;b.partial=partial
            with pytest.raises(PanStopped): await e.start()
        else:
            await e.start()
            b.fail=member;b.partial=partial
            if member=='Set':
                with pytest.raises(PanStopped): await e.open_pairing(owner_authorized=True)
            else:
                await e.open_pairing(owner_authorized=True)
                await e._pair_task
                await e.tick()
        assert e.blocked and b.closed
        assert not e.agent.authenticated
        assert not any(c[2]=='Set' and c[3][1]=='Trusted' for c in b.calls)
    run(scenario())


def test_pairing_window_expires_with_silent_backend_and_cancels():
    async def scenario():
        e,b,now = setup(pairing_window_s=15)
        original=b.call
        async def silent(*args):
            if args[3]=='Pair': await asyncio.Event().wait()
            return await original(*args)
        b.call=silent
        await e.start()
        await e.open_pairing(owner_authorized=True)
        await asyncio.sleep(0)
        now[0]=16
        await e.tick()
        assert e.agent.until==0 and not e.agent.authenticated
        assert any(c[2]=='CancelPairing' for c in b.calls)
        await e.stop()
    run(scenario())


def test_same_path_recreated_during_reply_does_not_admit_peer():
    async def scenario():
        e,b,_ = setup()
        await e.start()
        original=b.call
        async def replaced(*args):
            result=await original(*args)
            if args[3]=='Connect': b.generation+=1
            return result
        b.call=replaced
        await e.open_pairing(owner_authorized=True)
        await e._pair_task
        await e.tick()
        assert e.blocked and e.connected_iface is None
        assert not any(c[2]=='Disconnect' for c in b.calls)
        assert e.restored is False
    run(scenario())


def test_other_client_winning_connect_race_is_not_disconnected_or_adopted():
    from redux.connectivity.bluez_executor import PanCallFailed
    async def scenario():
        e,b,_=setup()
        await e.start()
        original=b.call
        async def race(*args):
            if args[3]=='Connect':
                b.network.update(Connected=True,Interface='bnep0')
                raise PanCallFailed('already_connected')
            return await original(*args)
        b.call=race
        await e.open_pairing(owner_authorized=True);await e._pair_task;await e.tick()
        assert e.blocked and e.connected_iface is None and e.restored is False
        assert not any(c[2]=='Disconnect' for c in b.calls)
    run(scenario())


def test_silent_pan_connect_times_out_and_verified_cleanup_runs():
    async def scenario():
        e,b,_=setup(connect_timeout_s=1)
        await e.start()
        original=b.call
        async def silent(*args):
            if args[3]=='Connect':await asyncio.Event().wait()
            return await original(*args)
        b.call=silent
        await e.open_pairing(owner_authorized=True);await e._pair_task;await e.tick()
        assert e.blocked and e.restored is True
        assert any(c[2]=='Disconnect' for c in b.calls)
    run(scenario())


def test_preexisting_pan_stops_before_any_agent_or_setting_change():
    async def scenario():
        e,b,_=setup();b.network['Connected']=True
        with pytest.raises(PanStopped):await e.start()
        assert not any(c[2] in ('Set','Disconnect','RegisterAgent','Connect') for c in b.calls)
    run(scenario())


def test_cancellation_during_operator_confirmation_cannot_grant_late_authentication():
    async def scenario():
        e,b,now=setup();a=e.agent;a.owner=b.name;a.until=10
        ready=asyncio.Event()
        async def confirm(_):await ready.wait();return True
        a.confirm=confirm
        task=asyncio.create_task(a.confirmation(b.name,PEER,123456))
        await asyncio.sleep(0);a.close();ready.set()
        with pytest.raises(PanStopped):await task
        assert not a.authenticated
    run(scenario())


def test_authenticated_new_pairing_waits_for_separate_trust_without_granting_it():
    async def scenario():
        e,b,_=setup();b.peer['Trusted']=False
        await e.start();await e.open_pairing(owner_authorized=True);await e._pair_task
        assert e.connected_iface is None and e._bond_pending
        await e.tick()
        assert not any(c[2]=='Connect' for c in b.calls)
        b.peer['Trusted']=True  # explicit external operator trust, never an executor write
        await e.tick()
        assert e.connected_iface=='bnep0'
        assert sum(c[2]=='Pair' for c in b.calls)==1
        assert not any(c[2]=='Set' and c[3][1]=='Trusted' for c in b.calls)
        await e.stop()
    run(scenario())


def test_existing_bond_requires_explicit_independent_authentication_provisioning():
    async def scenario():
        e,b,_=setup();await e.start()
        with pytest.raises(PanStopped):await e.admit_verified_bond(owner_authorized=True)
        assert not any(c[2] in ('Connect','Pair') for c in b.calls)
        await e.admit_verified_bond(owner_authorized=True,authenticated_bond_verified=True)
        assert e.connected_iface=='bnep0'
        assert not any(c[2]=='Pair' for c in b.calls)
        await e.stop()
    run(scenario())
