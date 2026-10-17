"""Opt-in, bounded PAN executor and authenticated outgoing pairing agent.

No auto-pairing, trust writes, default-agent takeover, adapter power changes,
NetworkManager, DHCP, firewall or service management. The optional bus is opened
only after enabled configuration. Public status never contains peer identity.
"""
import asyncio
from dataclasses import dataclass, field
import re
import time
import tomllib

from .bluez_pan import PanPeer, NAP, pan_management_ready
from .phone import PhoneSession, PhonePolicy, PhonePhase, PhoneIntent


class PanStopped(RuntimeError):
    pass


class PanCallFailed(PanStopped):
    def __init__(self, kind="other"):
        self.kind = kind
        super().__init__("BlueZ request failed")


@dataclass(frozen=True)
class PanConfig:
    enabled: bool = False
    adapter: str = '/org/bluez/hci0'
    peer: str = field(default='', repr=False)
    management_probe_peer: str = field(default="", repr=False)
    pairing_window_s: int = 90
    call_timeout_s: int = 2
    connect_timeout_s: int = 10

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('phone enabled must be a boolean')
        if self.enabled:
            import ipaddress
            ipaddress.ip_address(self.management_probe_peer)
        PhonePolicy(pairing_window_s=self.pairing_window_s, connect_timeout_s=self.connect_timeout_s)
        if type(self.call_timeout_s) is not int or not 1 <= self.call_timeout_s <= 5:
            raise ValueError('invalid BlueZ call timeout')
        if (not re.fullmatch(r'/org/bluez/hci[0-9]+', self.adapter)
                or self.enabled and not re.fullmatch(re.escape(self.adapter)+r'/dev_(?:[0-9A-F]{2}_){5}[0-9A-F]{2}', self.peer)):
            raise ValueError('invalid pinned Bluetooth identity')

    @classmethod
    def load(cls, path):
        with open(path, 'rb') as stream:
            values = tomllib.load(stream).get('phone', {})
        return cls(**values)


class PairingAgent:
    """Numeric comparison only; fixed peer, sender and finite owner-opened window."""
    def __init__(self, peer, confirm, *, clock=time.monotonic):
        self.peer, self.confirm, self.clock = peer, confirm, clock
        self.owner = None
        self.until = 0.0
        self.authenticated = False
        self.generation = 0

    def close(self):
        self.until = 0.0
        self.generation += 1

    def invalidate(self):
        self.close()
        self.authenticated = False

    async def confirmation(self, sender, peer, passkey):
        epoch = self.generation
        remaining = self.until - self.clock()
        if (sender != self.owner or peer != self.peer or not 0 < remaining <= 300
                or type(passkey) is not int or not 0 <= passkey <= 999999):
            raise PanStopped('pairing confirmation denied')
        accepted = await asyncio.wait_for(self.confirm(passkey), timeout=remaining)
        if (accepted is not True or self.clock() >= self.until or epoch != self.generation):
            raise PanStopped('pairing confirmation expired or rejected')
        self.authenticated = True

    async def authorize(self, sender, peer, uuid):
        if (sender != self.owner or peer != self.peer or not self.authenticated
                or not isinstance(uuid, str) or uuid.lower() != NAP):
            raise PanStopped('service authorization denied')
        verified = await self.peer_evidence()
        if not verified.eligible():
            raise PanStopped('untrusted PAN service denied')


class BluezPanExecutor:
    def __init__(self, config, bus, confirm, route_verified, *, clock=time.monotonic):
        self.config, self.bus, self.clock = config, bus, clock
        self.route_verified = route_verified
        self.session = PhoneSession(PhonePolicy(pairing_window_s=config.pairing_window_s,
                                               connect_timeout_s=config.connect_timeout_s))
        self.agent = PairingAgent(config.peer, confirm, clock=clock)
        self.agent.peer_evidence = self._peer
        self.owner = None
        self.started = self.blocked = False
        self.changed = set()
        self.expected_properties = {}
        self.own_pan = False
        self.baseline = {}
        self.connected_iface = None
        self.restored = None
        self._pair_task = None
        self._deadline = float('inf')
        self.peer_epoch = None
        self.adapter_epoch = None
        self._pair_pending = False
        self._bond_pending = False

    async def _owner(self):
        budget = min(self.config.call_timeout_s, self._deadline - self.clock())
        if budget <= 0:
            raise PanStopped("PAN phase deadline")
        return await asyncio.wait_for(self.bus.owner(), budget)

    async def _call(self, path, interface, member, signature='', body=(), *, timeout=None):
        if not self.config.enabled or self.blocked:
            raise PanStopped('PAN disabled or interlocked')
        if ((path == self.config.peer and self.peer_epoch is not None
                and self.bus.epoch(path) != self.peer_epoch)
                or (path == self.config.adapter and self.adapter_epoch is not None
                    and self.bus.epoch(path) != self.adapter_epoch)):
            self.agent.invalidate()
            raise PanStopped('Bluetooth object generation changed')
        if await self._owner() != self.owner:
            self.agent.invalidate()
            raise PanStopped('BlueZ bus owner changed')
        budget = min(timeout or self.config.call_timeout_s, self._deadline - self.clock())
        if budget <= 0:
            raise PanStopped('PAN phase deadline')
        result = await asyncio.wait_for(self.bus.call(self.owner, path, interface, member, signature, list(body)), budget)
        if (await self._owner() != self.owner
                or path == self.config.peer and self.peer_epoch is not None and self.bus.epoch(path) != self.peer_epoch
                or path == self.config.adapter and self.adapter_epoch is not None and self.bus.epoch(path) != self.adapter_epoch):
            self.agent.invalidate()
            raise PanStopped('BlueZ response generation changed')
        return result

    async def _get(self, path, interface):
        result = await self._call(path, 'org.freedesktop.DBus.Properties', 'GetAll', 's', (interface,))
        if not isinstance(result, dict):
            raise PanStopped('invalid BlueZ property snapshot')
        return result

    async def _set(self, name, value):
        self.changed.add(name)  # before possibly partial write
        self.expected_properties[name] = value
        await self._call(self.config.adapter, 'org.freedesktop.DBus.Properties', 'Set',
                         'ssv', ('org.bluez.Adapter1', name, value))
        actual = await self._get(self.config.adapter, 'org.bluez.Adapter1')
        if type(actual.get(name)) is not type(value) or actual[name] != value:
            raise PanStopped('BlueZ property write not verified')

    async def _peer(self):
        if self.peer_epoch is not None and self.bus.epoch(self.config.peer) != self.peer_epoch:
            self.agent.invalidate()
            raise PanStopped('Bluetooth peer object generation changed')
        p = await self._get(self.config.peer, 'org.bluez.Device1')
        # Device path plus Adapter and Address must agree, even after object replacement.
        if self.peer_epoch is not None and self.bus.epoch(self.config.peer) != self.peer_epoch:
            self.agent.invalidate()
            raise PanStopped('Bluetooth peer snapshot generation changed')
        expected = self.config.peer.rsplit('/dev_', 1)[-1].replace('_', ':')
        if p.get('Adapter') != self.config.adapter or p.get('Address') != expected:
            raise PanStopped('Bluetooth peer identity changed')
        return PanPeer(self.config.peer, p.get('Paired') is True and p.get('Bonded') is True,
                       p.get('Trusted') is True, self.agent.authenticated,
                       NAP in p.get('UUIDs', ()))

    async def start(self):
        if not self.config.enabled:
            return False  # no bus access/import/controller action when disabled
        if self.started or self.blocked:
            raise PanStopped('PAN executor cannot restart automatically')
        try:
            self.owner = await self._owner()
            if not isinstance(self.owner, str) or not re.fullmatch(r':[0-9]+\.[0-9]+', self.owner):
                raise PanStopped('BlueZ unique owner unavailable')
            self.agent.owner = self.owner
            self._deadline = self.clock() + 20
            self.baseline = await self._get(self.config.adapter, 'org.bluez.Adapter1')
            if (self.baseline.get('Powered') is not True or self.baseline.get('Pairable') is not False
                    or self.baseline.get('Discoverable') is not False):
                raise PanStopped('exclusive closed powered adapter baseline required')
            if any(type(self.baseline.get(k)) is not int or not 0 <= self.baseline[k] <= 0xffffffff
                   for k in ('PairableTimeout', 'DiscoverableTimeout')):
                raise PanStopped('adapter timeout baseline unavailable')
            await self._peer()
            network = await self._get(self.config.peer, 'org.bluez.Network1')
            if network.get('Connected') is not False:
                raise PanStopped('preexisting PAN connection is not owned')
            await asyncio.wait_for(self.bus.export_agent(self.agent), self.config.call_timeout_s)
            self.peer_epoch = self.bus.epoch(self.config.peer)
            self.adapter_epoch = self.bus.epoch(self.config.adapter)
            if await self._get(self.config.adapter, "org.bluez.Adapter1") != self.baseline:
                raise PanStopped("adapter changed during agent registration")
            await self._peer()
            self.changed.add('agent')
            await self._call('/org/bluez', 'org.bluez.AgentManager1', 'RegisterAgent', 'os',
                             ('/org/redux/PhoneAgent', 'DisplayYesNo'))
            self.started = True
            self.session.enable(now=self.clock())
            return True
        except BaseException:
            await self.stop()
            raise PanStopped('PAN startup failed; inspect sanitized restoration status') from None
        finally:
            self._deadline = float('inf')

    async def open_pairing(self, *, owner_authorized=False):
        if owner_authorized is not True or not self.started or self.blocked:
            raise PanStopped('explicit enabled pairing required')
        try:
            self.session.open_pairing(owner_authorized=True, now=self.clock())
            self.agent.invalidate()
            self.agent.until = self.clock() + self.config.pairing_window_s
            self._deadline = self.agent.until
            # Outgoing-only pairing: NEVER open the daemon's global incoming
            # agent path or default-agent registry. Incoming pairing stays off.
            await self._set('PairableTimeout', self.config.pairing_window_s)
            await self._set('DiscoverableTimeout', self.config.pairing_window_s)
            self._pair_task = asyncio.create_task(self._pair())
        except BaseException:
            await self.stop()
            raise PanStopped('pairing setup failed') from None
        finally:
            self._deadline = float('inf')

    async def _pair(self):
        try:
            self._pair_pending = True
            await self._call(self.config.peer, 'org.bluez.Device1', 'Pair',
                             timeout=max(0.001, self.agent.until-self.clock()))
            self._pair_pending = False
            peer = await self._peer()
            authenticated = peer.authenticated_bond and peer.paired and self.clock() < self.agent.until
            self.session.pairing_result(authenticated=authenticated, trusted=peer.trusted, now=self.clock())
            self._bond_pending = authenticated and not peer.eligible()
            if not authenticated:
                self.agent.invalidate()
            self.agent.close()
            if peer.eligible() and self.session.status(now=self.clock())['trusted_bond_verified']:
                await self._connect()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.agent.invalidate()
            self.blocked = True

    async def admit_verified_bond(self, *, owner_authorized=False, authenticated_bond_verified=False):
        """Operator/UI provisioning hook, NOT inferred from Trusted or config.

        A trusted bond from a previous process needs independent authenticated
        provisioning evidence; no automatic import based on an address alone.
        """
        if (not self.started or self.blocked or owner_authorized is not True
                or authenticated_bond_verified is not True):
            raise PanStopped('explicit authenticated bond provisioning required')
        try:
            peer = await self._peer()
            self.session.admit_verified_bond(owner_authorized=True, authenticated=True,
                                             trusted=peer.trusted and peer.paired, now=self.clock())
            self.agent.authenticated = True
            await self._connect()
        except BaseException:
            await self.stop()
            raise PanStopped('verified bond provisioning failed') from None

    async def _connect(self):
        peer = await self._peer()
        if not peer.eligible():
            raise PanStopped('authenticated trusted peer evidence unavailable')
        existing = await self._get(self.config.peer, 'org.bluez.Network1')
        if existing.get('Connected') is not False or self.own_pan:
            raise PanStopped('PAN already owned; refusing connection adoption')
        self.own_pan = True  # before an in-flight/partial Connect
        try:
            iface = await self._call(self.config.peer, 'org.bluez.Network1', 'Connect', 's', ('nap',),
                                     timeout=self.config.connect_timeout_s)
        except PanCallFailed as exc:
            if exc.kind == 'already_connected':
                self.own_pan = False
            raise
        peer = await self._peer()
        properties = await self._get(self.config.peer, 'org.bluez.Network1')
        route = await asyncio.wait_for(self.route_verified(iface), self.config.call_timeout_s)
        if not pan_management_ready(peer, iface, properties, route_verified=route):
            raise PanStopped('PAN outcome or management route unverified')
        self.connected_iface = iface
        self.session.link_connected(authenticated=True, trusted=True, now=self.clock())

    async def tick(self):
        if not self.started or self.blocked:
            if self.blocked and self.started:
                await self.stop()
            return
        try:
            if await self._owner() != self.owner:
                raise PanStopped('BlueZ bus owner changed')
            adapter = await self._get(self.config.adapter, 'org.bluez.Adapter1')
            if (adapter.get('Pairable') is not False or adapter.get('Discoverable') is not False
                    or adapter.get('Powered') is not True):
                raise PanStopped('unexpected Bluetooth adapter ownership change')
            if self.session.phase is PhonePhase.CONNECTED:
                peer = await self._peer()
                network = await self._get(self.config.peer, 'org.bluez.Network1')
                if not peer.eligible():
                    raise PanStopped('Bluetooth trust or identity revoked')
                if network.get('Connected') is False:
                    self.own_pan = False
                    self.connected_iface = None
                    self.session.link_lost(now=self.clock())
                elif not pan_management_ready(peer, self.connected_iface, network,
                         route_verified=await asyncio.wait_for(self.route_verified(self.connected_iface), self.config.call_timeout_s)):
                    raise PanStopped('PAN management continuity lost')
            if self._bond_pending and self.session.phase is PhonePhase.IDLE:
                peer = await self._peer()
                if not peer.paired or not peer.authenticated_bond:
                    raise PanStopped('pending authenticated bond changed')
                if peer.eligible():
                    self.session.admit_verified_bond(owner_authorized=True, authenticated=True,
                                                     trusted=True, now=self.clock())
                    self._bond_pending = False
                    await self._connect()
            intents = self.session.tick(now=self.clock())
            if PhoneIntent.STOP_DISCOVERABLE in intents:
                self.agent.close()
                if self._pair_task is not None and not self._pair_task.done():
                    self._pair_task.cancel()
                    await self._call(self.config.peer, 'org.bluez.Device1', 'CancelPairing')
                    self._pair_pending = False
            if PhoneIntent.CONNECT_TRUSTED in intents:
                try:
                    await self._connect()
                except Exception:
                    if not self.own_pan:
                        raise
                    await self._call(self.config.peer, 'org.bluez.Network1', 'Disconnect')
                    self.own_pan = False
                    self.session.connect_failed(now=self.clock())
            if PhoneIntent.DISCONNECT in intents and self.own_pan:
                await self._call(self.config.peer, 'org.bluez.Network1', 'Disconnect')
                self.own_pan = False
        except BaseException:
            await self.stop()

    async def stop(self):
        """Only our pinned connection/properties; never adopt a replacement daemon."""
        if not self.config.enabled:
            return
        self.agent.invalidate()
        task = self._pair_task
        pairing_pending = task is not None and not task.done()
        if pairing_pending and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        okay = True
        try:
            if self.owner is None or await self._owner() != self.owner:
                raise PanStopped('original daemon gone; rollback unverified')
            self.blocked = False  # permit only bounded cleanup on original owner
            self._deadline = self.clock() + 10
            if self.own_pan:
                await self._call(self.config.peer, 'org.bluez.Network1', 'Disconnect')
                if (await self._get(self.config.peer, 'org.bluez.Network1')).get('Connected') is not False:
                    raise PanStopped('PAN disconnect unverified')
            if self._pair_pending:
                await self._call(self.config.peer, 'org.bluez.Device1', 'CancelPairing')
                self._pair_pending = False
            for name in ('PairableTimeout', 'DiscoverableTimeout'):
                if name in self.changed:
                    actual = await self._get(self.config.adapter, 'org.bluez.Adapter1')
                    if actual.get(name) not in (self.expected_properties[name], self.baseline[name]):
                        raise PanStopped('adapter timeout ownership changed')
                    if actual.get(name) != self.baseline[name]:
                        await self._set(name, self.baseline[name])
            if 'agent' in self.changed:
                await self._call('/org/bluez', 'org.bluez.AgentManager1', 'UnregisterAgent', 'o', ('/org/redux/PhoneAgent',))
            if self.changed:
                state = await self._get(self.config.adapter, 'org.bluez.Adapter1')
                if state.get('Pairable') is not False or state.get('Discoverable') is not False:
                    raise PanStopped('closed adapter baseline not restored')
            if self.started and (await self._get(self.config.peer, 'org.bluez.Network1')).get('Connected') is not False:
                raise PanStopped('original PAN baseline changed')
        except BaseException:
            okay = False
        finally:
            await self.bus.close()  # releases only this client's exported agent/PAN
            self.started = False
            self.own_pan = False
            self.connected_iface = None
            self.blocked = True
            self.restored = okay

    def status(self):
        return {'enabled': self.config.enabled, 'started': self.started,
                'blocked': self.blocked, 'management_ready': self.connected_iface is not None,
                'restoration_verified': self.restored}


async def run_executor(executor, stop, *, pair=False):
    """No retry after interlock/owner change; finally always closes our client."""
    try:
        if not await executor.start():
            return
        if pair:
            await executor.open_pairing(owner_authorized=True)
        while not stop.is_set() and not executor.blocked:
            await executor.tick()
            try:
                await asyncio.wait_for(stop.wait(), 0.25)
            except asyncio.TimeoutError:
                pass
    finally:
        if executor.started:
            await executor.stop()


async def _confirm_console(passkey):
    import sys
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return False
    # Never send pairing secrets to journals/pipes or a background input thread.
    print(f'Compare {passkey:06d} on the pinned phone. Type yes to confirm:', flush=True)
    loop = asyncio.get_running_loop()
    result = loop.create_future()
    def readable():
        if not result.done():
            result.set_result(sys.stdin.readline().strip().lower() == 'yes')
    loop.add_reader(sys.stdin, readable)
    try:
        return await result
    finally:
        loop.remove_reader(sys.stdin)


def main(argv=None):
    import argparse
    import json
    import signal
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/redux/live.toml')
    parser.add_argument('--pairing', action='store_true', help='explicit outgoing numeric pairing on pinned peer')
    parser.add_argument('--check', action='store_true', help='configuration-only, no bus access')
    args = parser.parse_args(argv)
    try:
        config = PanConfig.load(args.config)
    except (OSError, ValueError, TypeError):
        print('{"blocked":true,"error":"Invalid private PAN configuration"}')
        return 2
    if not config.enabled or args.check:
        print(json.dumps({'enabled': config.enabled, 'bus_opened': False}))
        return 0
    async def run():
        from .bluez_bus import BluezBus
        bus = await BluezBus.connect()
        async def route(iface):
            if not isinstance(iface, str) or not re.fullmatch(r'bnep[0-9]{1,4}', iface):
                return False
            import json
            process = await asyncio.create_subprocess_exec('ip', '-j', 'route', 'get',
                config.management_probe_peer, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                raw, _ = await asyncio.wait_for(process.communicate(), 1)
                records = json.loads(raw)
                return process.returncode == 0 and len(records) == 1 and records[0].get('dev') == iface
            finally:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
        executor = BluezPanExecutor(config, bus, _confirm_console, route)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        try:
            await run_executor(executor, stop, pair=args.pairing)
        finally:
            if not executor.started:
                await bus.close()
        print(json.dumps(executor.status(), sort_keys=True))
        return 0 if executor.restored is not False else 3
    try:
        return asyncio.run(run())
    except Exception:
        print('{"blocked":true,"error":"PAN executor stopped"}')
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
