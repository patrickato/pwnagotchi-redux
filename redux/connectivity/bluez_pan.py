"""Offline BlueZ PAN contract: declarative requests and verified outcomes only.

No D-Bus client, agent, discovery, trust setter, network setup or service is
installed. Device paths contain identifiers: never expose these request objects
via logs/Doctor; public results contain only fixed states. PAN is management-only.
"""
from dataclasses import dataclass, field
import re

NAP = '00001116-0000-1000-8000-00805f9b34fb'


@dataclass(frozen=True)
class PanPeer:
    path: str = field(repr=False)
    paired: bool = False
    trusted: bool = False
    authenticated_bond: bool = False  # independent security/agent evidence, NOT inferred from Trusted
    nap_advertised: bool = False

    def eligible(self):
        return (isinstance(self.path, str) and re.fullmatch(
            r'/org/bluez/hci[0-9]+/dev_(?:[0-9A-F]{2}_){5}[0-9A-F]{2}', self.path)
            and self.paired is True and self.trusted is True
            and self.authenticated_bond is True and self.nap_advertised is True)


@dataclass(frozen=True)
class PanRequest:
    path: str = field(repr=False)
    interface: str = 'org.bluez.Network1'
    method: str = 'Connect'
    arguments: tuple = ('nap',)
    timeout_s: int = 10


def plan_pan_connect(peer: PanPeer, *, enabled=False, reconnect_due=False) -> PanRequest | None:
    """A reconnect intent is not a successful PAN or authenticated IP link."""
    if enabled is not True or reconnect_due is not True or not peer.eligible():
        return None
    return PanRequest(peer.path)


def pan_management_ready(peer: PanPeer, returned_iface, properties, *,
                         route_verified=False) -> bool:
    """Network1 outcome must match the Connect result; IP/security are separate.

    The same private peer identity must be pinned by the eventual bus adapter
    through PropertiesChanged, bus-owner replacement and late replies. This
    function accepts only properties measured on that peer's Network1 object.
    """
    return bool(peer.eligible() and isinstance(returned_iface, str)
                and re.fullmatch(r'bnep[0-9]{1,4}', returned_iface)
                and isinstance(properties, dict) and properties.get('Connected') is True
                and properties.get('Interface') == returned_iface
                and properties.get('UUID') in ('nap', NAP)
                and route_verified is True)


def pairing_properties(*, window_open: bool, seconds_left: int) -> tuple:
    """Prepare Adapter1 property writes; timeouts BEFORE enabling either flag.

    A future executor must own the adapter and verify each property. Errors
    stop opening and close both flags; adapter-wide changes need approval.
    No pairing agent capability or silent trust grant is implied here.
    """
    if window_open is not True:
        return (('Pairable', False), ('Discoverable', False))
    if type(seconds_left) is not int or not 1 <= seconds_left <= 300:
        raise ValueError('finite pairing window required')
    return (('PairableTimeout', seconds_left), ('DiscoverableTimeout', seconds_left),
            ('Pairable', True), ('Discoverable', True))
