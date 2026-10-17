"""Actual dbus-next message encoding and Agent1 dispatch on a fake connection."""
import asyncio
import pytest

from dbus_next import Message, MessageType, Variant, MessageFlag
from redux.connectivity.bluez_bus import BluezBus
from redux.connectivity.bluez_executor import PanCallFailed, PairingAgent, PanStopped


class Connection:
    def __init__(self):self.messages=[];self.replies=[];self.handler=None;self.disconnected=0
    async def call(self,message):
        self.messages.append(message)
        if message.member=='GetNameOwner':
            return Message(message_type=MessageType.METHOD_RETURN,reply_serial=1,sender='org.freedesktop.DBus',signature='s',body=[':1.7'])
        if message.member=='GetAll':
            return Message(message_type=MessageType.METHOD_RETURN,reply_serial=1,sender=':1.7',signature='a{sv}',body=[{'Connected':Variant('b',False)}])
        return Message(message_type=MessageType.METHOD_RETURN,reply_serial=1,sender=':1.7')
    def add_message_handler(self,handler):self.handler=handler
    def remove_message_handler(self,handler):assert handler==self.handler;self.handler=None
    def send(self,message):self.replies.append(message)
    def disconnect(self):self.disconnected+=1


def test_transport_encodes_variants_no_auto_start_and_owner_checks():
    async def scenario():
        connection=Connection();bus=BluezBus(connection)
        assert await bus.owner()==':1.7'
        await bus.call(':1.7','/org/bluez/hci0','org.freedesktop.DBus.Properties','Set','ssv',['org.bluez.Adapter1','PairableTimeout',30])
        message=connection.messages[-1]
        assert message.flags & MessageFlag.NO_AUTOSTART
        assert message.body[-1].signature=='u' and message.body[-1].value==30
        result=await bus.call(':1.7','/org/bluez/hci0','org.freedesktop.DBus.Properties','GetAll','s',['org.bluez.Network1'])
        assert result=={'Connected':False}
        with pytest.raises(PanStopped):
            await bus.call(':1.8','/org/bluez/hci0','org.freedesktop.DBus.Properties','GetAll','s',['org.bluez.Network1'])
        await bus.close();await bus.close()
        assert connection.disconnected==1
    asyncio.run(scenario())


def test_agent_sender_legacy_methods_and_epoch_tracking():
    async def scenario():
        connection=Connection();bus=BluezBus(connection)
        async def yes(_):return True
        peer='/org/bluez/hci0/dev_00_11_22_33_44_55'
        agent=PairingAgent(peer,yes,clock=lambda:1)
        agent.owner=':1.7';agent.until=10
        await bus.export_agent(agent)
        for sender,member,signature,body in [
            (':1.99','RequestConfirmation','ou',[peer,123456]),
            (':1.7','RequestPinCode','o',[peer]),
            (':1.7','RequestAuthorization','o',[peer]),
        ]:
            message=Message(path='/org/redux/PhoneAgent',interface='org.bluez.Agent1',member=member,
                            signature=signature,body=body,sender=sender,serial=1)
            assert connection.handler(message) is True
            await asyncio.sleep(0);await asyncio.sleep(0)
            assert connection.replies[-1].message_type==MessageType.ERROR
        message=Message(path='/org/redux/PhoneAgent',interface='org.bluez.Agent1',member='RequestConfirmation',
                        signature='ou',body=[peer,123456],sender=':1.7',serial=1)
        connection.handler(message)
        for _ in range(5):await asyncio.sleep(0)
        assert agent.authenticated
        assert connection.replies[-1].message_type==MessageType.METHOD_RETURN
        event=Message(message_type=MessageType.SIGNAL,sender=':1.7',path='/',
                      interface='org.freedesktop.DBus.ObjectManager',member='InterfacesRemoved',
                      signature='oas',body=[peer,['org.bluez.Device1']])
        connection.handler(event)
        assert bus.epoch(peer)==1
        await bus.close()
    asyncio.run(scenario())


def test_already_connected_error_is_classified_without_leaking_backend_text():
    reply=Message(message_type=MessageType.ERROR,reply_serial=1,error_name='org.bluez.Error.AlreadyConnected',
                  signature='s',body=['PRIVATE RADIO IDENTIFIER'])
    with pytest.raises(PanCallFailed) as exc:BluezBus._reply(reply)
    assert exc.value.kind=='already_connected'
    assert 'PRIVATE' not in str(exc.value)
