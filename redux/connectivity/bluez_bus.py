"""Optional long-lived dbus-next transport. No import connects to a bus."""
import asyncio

from .bluez_executor import PanStopped, PanCallFailed


class BluezBus:
    def __init__(self, connection):
        self.connection = connection
        self.tasks = set()
        self.handler = None
        self.closed = False
        self.epochs = {}

    @classmethod
    async def connect(cls):
        from dbus_next.aio import MessageBus
        from dbus_next import BusType
        return cls(await asyncio.wait_for(MessageBus(bus_type=BusType.SYSTEM).connect(), 5))

    async def owner(self):
        from dbus_next import Message
        reply = await self.connection.call(Message(destination='org.freedesktop.DBus',
            path='/org/freedesktop/DBus', interface='org.freedesktop.DBus',
            member='GetNameOwner', signature='s', body=['org.bluez']))
        self._reply(reply)
        return reply.body[0]

    @staticmethod
    def _reply(reply):
        from dbus_next import MessageType
        if reply is None or reply.message_type != MessageType.METHOD_RETURN:
            kind = 'already_connected' if reply is not None and reply.error_name == 'org.bluez.Error.AlreadyConnected' else 'other'
            raise PanCallFailed(kind)

    @staticmethod
    def _plain(value):
        from dbus_next import Variant
        if isinstance(value, Variant):
            return BluezBus._plain(value.value)
        if isinstance(value, dict):
            return {k: BluezBus._plain(v) for k,v in value.items()}
        if isinstance(value, list):
            return [BluezBus._plain(v) for v in value]
        return value

    async def call(self, owner, path, interface, member, signature, body):
        from dbus_next import Message, MessageFlag, Variant
        if signature == 'ssv':
            value = body[2]
            body = [*body[:2], Variant('b' if type(value) is bool else 'u', value)]
        reply = await self.connection.call(Message(destination=owner, path=path,
            interface=interface, member=member, signature=signature, body=body,
            flags=MessageFlag.NO_AUTOSTART))
        self._reply(reply)
        if reply.sender != owner:
            raise PanStopped('D-Bus response owner mismatch')
        values = self._plain(reply.body)
        return values[0] if len(values) == 1 else None

    def epoch(self, path):
        return self.epochs.get(path, 0)

    async def export_agent(self, agent):
        from dbus_next import Message, MessageType
        reply = await self.connection.call(Message(destination='org.freedesktop.DBus',
            path='/org/freedesktop/DBus', interface='org.freedesktop.DBus', member='AddMatch',
            signature='s', body=["type='signal',interface='org.freedesktop.DBus.ObjectManager',sender='org.bluez'"]))
        self._reply(reply)
        async def dispatch(message):
            try:
                if message.sender != agent.owner:
                    raise PanStopped('agent caller denied')
                if message.member == 'RequestConfirmation' and message.signature == 'ou':
                    await agent.confirmation(message.sender, *message.body)
                elif message.member == 'AuthorizeService' and message.signature == 'os':
                    await agent.authorize(message.sender, *message.body)
                elif message.member in ('Cancel', 'Release') and message.signature == '':
                    agent.invalidate()
                else:
                    # Reject PIN, Just Works, legacy passkeys and unknown methods.
                    raise PanStopped('agent method denied')
                self.connection.send(Message.new_method_return(message))
            except BaseException:
                self.connection.send(Message.new_error(message, 'org.bluez.Error.Rejected', 'Pairing denied'))
        def handle(message):
            if (message.message_type == MessageType.SIGNAL and message.sender == agent.owner
                    and message.interface == 'org.freedesktop.DBus.ObjectManager'
                    and message.member in ('InterfacesAdded', 'InterfacesRemoved') and message.body):
                path = message.body[0]
                self.epochs[path] = self.epoch(path) + 1
            if (message.message_type != MessageType.METHOD_CALL
                    or message.path != '/org/redux/PhoneAgent'
                    or message.interface != 'org.bluez.Agent1'):
                return None
            task = asyncio.create_task(dispatch(message))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return True
        self.handler = handle
        self.connection.add_message_handler(handle)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.handler is not None:
            self.connection.remove_message_handler(self.handler)
            self.handler = None
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        self.connection.disconnect()
