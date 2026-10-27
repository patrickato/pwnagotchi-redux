"""LAN service discovery → labeled inventory."""
from redux.netrecon.discovery import (
    parse_avahi_browse, parse_ssdp, label_inventory, DiscoveredHost, LanDiscovery,
)

AVAHI = "\n".join([
    "=;eth0;IPv4;Living Room;_googlecast._tcp;local;Living-Room.local;192.168.1.50;8009;id=abc",
    "=;eth0;IPv4;Office HP;_ipp._tcp;local;HP-LaserJet.local;192.168.1.52;631;ty=HP LaserJet",
    "+;eth0;IPv4;ignored unresolved line",
])
SSDP = ("HTTP/1.1 200 OK\r\nLOCATION: http://192.168.1.51:1400/xml/device_description.xml\r\n"
        "SERVER: Linux UPnP/1.0 Sonos/1\r\nST: urn:schemas-upnp-org:device:ZonePlayer:1\r\n\r\n")


def test_parse_avahi_keys_by_ip_with_services():
    hosts = parse_avahi_browse(AVAHI)
    assert set(hosts) == {"192.168.1.50", "192.168.1.52"}
    assert "_googlecast._tcp" in hosts["192.168.1.50"].mdns_services
    assert hosts["192.168.1.52"].hostname == "HP-LaserJet.local"


def test_parse_ssdp_captures_server_and_type():
    hosts = parse_ssdp(SSDP)
    assert "192.168.1.51" in hosts
    assert "Sonos" in hosts["192.168.1.51"].ssdp


def test_label_inventory_identifies_each_host():
    hosts = list(parse_avahi_browse(AVAHI).values()) + list(parse_ssdp(SSDP).values())
    inv = label_inventory(hosts)
    cats = {r["ip"]: r["identity"]["category"] for r in inv["hosts"]}
    assert cats["192.168.1.50"] == "streamer"       # Chromecast via _googlecast
    assert cats["192.168.1.52"] == "printer"        # _ipp
    assert cats["192.168.1.51"] == "speaker"        # Sonos via SSDP
    assert inv["identified"] == 3


def test_label_host_carries_actions():
    hosts = parse_avahi_browse(AVAHI)
    inv = label_inventory(list(hosts.values()))
    printer = next(r for r in inv["hosts"] if r["ip"] == "192.168.1.52")
    assert any("IPP" in a["do"] or "9100" in a["do"] for a in printer["identity"]["actions"])


def test_discover_merges_mdns_and_ssdp_and_gates():
    # FIELD without authorization → refused
    inv, reason = LanDiscovery().discover(avahi_raw=AVAHI, ssdp_raw=SSDP)
    assert inv is None and "refused" in reason
    # LAB → runs, merges both sources
    inv, reason = LanDiscovery().discover(avahi_raw=AVAHI, ssdp_raw=SSDP, lab=True)
    assert inv["total"] == 3 and inv["identified"] == 3


def test_scope_gate_blocks_when_lan_not_armed():
    class _Scope:
        def authorize(self, *, ip=None, now=None):
            return (False, "scope empty")
    inv, reason = LanDiscovery().discover(avahi_raw=AVAHI, scope=_Scope())
    assert inv is None and "refused" in reason
