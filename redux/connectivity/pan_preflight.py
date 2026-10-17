"""Read-only PAN provisioning gates. No agent, pairing, trust or connection calls."""
import ipaddress
import json
import re

from .bluez_pan import NAP
from redux.radio.local_evidence import Unavailable


def gates(facts):
    """Every claim is independent; strict boolean, missing facts stay UNKNOWN."""
    names = ('daemon_identity_stable', 'peer_identity_verified', 'adapter_closed',
             'paired', 'trusted', 'bonded', 'authenticated_bond', 'network_connected',
             'network_identity_verified', 'ip_route_verified', 'redux_session_authenticated', 'object_generation_verified')
    return {name: 'PASS' if facts.get(name) is True else
            'FAIL' if facts.get(name) is False else 'UNKNOWN' for name in names}


def collect_pan(run, peer_path, route_peer, *, adapter='/org/bluez/hci0'):
    """Pin unique daemon owner, adapter/address and properties twice.

    BlueZ Bonded/Trusted NEVER authenticates a bond. No persisted file, HTTP
    response, config flag or Network1 connection is accepted as app-session or
    independent bond authentication. Those gates intentionally remain UNKNOWN.
    """
    if (not re.fullmatch(r'/org/bluez/hci[0-9]+', adapter) or
            not re.fullmatch(re.escape(adapter)+r'/dev_(?:[0-9A-F]{2}_){5}[0-9A-F]{2}', peer_path)):
        raise Unavailable('pan_identity_invalid')
    ipaddress.ip_address(route_peer)
    owner_cmd = ['busctl', '--system', '--auto-start=no', 'call', 'org.freedesktop.DBus',
                 '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetNameOwner', 's', 'org.bluez']

    def owner():
        raw = run(owner_cmd)
        if not raw.startswith('s '):
            raise Unavailable('pan_owner_unknown')
        value = json.loads(raw[2:])
        if not isinstance(value, str) or not re.fullmatch(r':[0-9]+\.[0-9]+', value):
            raise Unavailable('pan_owner_unknown')
        return value

    pinned = owner()

    def prop(path, interface, name, kind):
        raw = run(['busctl', '--system', '--auto-start=no', 'get-property', pinned,
                   path, 'org.bluez.'+interface, name])
        if not raw.startswith(kind+' '):
            raise Unavailable('pan_property_invalid')
        value = json.loads(raw[2:])
        if (kind == 'b' and type(value) is not bool or
                kind in ('s', 'o') and not isinstance(value, str)):
            raise Unavailable('pan_property_invalid')
        return value

    def snapshot():
        a = {n: prop(adapter, 'Adapter1', n, 'b') for n in ('Powered', 'Pairable', 'Discoverable')}
        d = {n: prop(peer_path, 'Device1', n, 'b') for n in ('Paired', 'Trusted', 'Bonded')}
        d['Adapter'] = prop(peer_path, 'Device1', 'Adapter', 'o')
        d['Address'] = prop(peer_path, 'Device1', 'Address', 's')
        # Network1 can legitimately be absent until provisioning; leave unknown.
        try:
            network = {n: prop(peer_path, 'Network1', n, kind) for n, kind in
                       (('Connected', 'b'), ('Interface', 's'), ('UUID', 's'))}
        except Unavailable:
            network = {}
        return a, d, network

    first = snapshot()
    a, d, n = first
    identity = (d['Adapter'] == adapter and d['Address'] == peer_path.rsplit('dev_', 1)[1].replace('_', ':'))
    iface = n.get('Interface')
    network_identity = (bool(re.fullmatch(r'bnep[0-9]{1,4}', iface or ''))
                        and n.get('UUID') in ('nap', NAP)) if n else None
    route = None
    route_snapshot = None
    def routing():
        return (json.loads(run(['ip', '-j', 'route', 'get', route_peer])),
                json.loads(run(['ip', '-j', 'addr', 'show', 'dev', iface])))
    if n.get('Connected') is True and network_identity:
        route_snapshot = routing()
        paths, addresses = route_snapshot
        source = paths[0].get('prefsrc') if len(paths) == 1 else None
        try:
            ipaddress.ip_address(source)
            source_valid = True
        except ValueError:
            source_valid = False
        route = (source_valid and len(paths) == 1 and paths[0].get('dev') == iface
                 and paths[0].get('type', 'unicast') == 'unicast' and len(addresses) == 1
                 and addresses[0].get('ifname') == iface
                 and type(addresses[0].get('ifindex')) is int
                 and addresses[0]['ifindex'] > 0 and 'UP' in addresses[0].get('flags', [])
                 and source in [a.get('local') for a in addresses[0].get('addr_info', [])])
    if (first != snapshot() or pinned != owner()
            or route_snapshot is not None and route_snapshot != routing()):
        # No claims from a replaced daemon, object or disconnected route.
        raise Unavailable('pan_observation_changed')
    return dict(daemon_identity_stable=True, peer_identity_verified=identity,
                adapter_closed=a['Powered'] and not a['Pairable'] and not a['Discoverable'],
                paired=d['Paired'], trusted=d['Trusted'], bonded=d['Bonded'],
                authenticated_bond=None, network_connected=n.get('Connected'),
                network_identity_verified=network_identity, ip_route_verified=route,
                redux_session_authenticated=None, object_generation_verified=None)
