"""Lab-mode auto-detect — seed the Scope from the device's own kit, safely.

Pins the safety line: only provably-yours things auto-arm (your private subnet,
your own radios, an AP you broadcast); a public uplink range is proposed but NOT
armed; neighbours never appear because this reads the device's own interfaces.
"""
from redux.core.labscope import (
    LabFacts, propose_lab_scope, parse_ip_json, parse_iw_dev, collect_lab_facts,
)
from redux.core import Scope


# --- the proposer (pure) ----------------------------------------------------- #

def test_private_subnet_is_accepted_and_collapsed_to_its_network():
    props = propose_lab_scope(LabFacts(cidrs=[("192.168.1.42/24", "eth0")]))
    assert len(props) == 1
    p = props[0]
    assert p.kind == "cidr" and p.value == "192.168.1.0/24" and p.accept is True
    assert "eth0" in p.reason


def test_public_range_is_proposed_but_not_armed():
    props = propose_lab_scope(LabFacts(cidrs=[("8.8.8.8/24", "wwan0")]))
    assert len(props) == 1 and props[0].accept is False
    assert "public" in props[0].reason.lower()


def test_loopback_and_link_local_are_skipped():
    props = propose_lab_scope(LabFacts(cidrs=[("127.0.0.1/8", "lo0"),
                                              ("169.254.5.5/16", "eth0")]))
    assert all(p.accept is False for p in props)
    assert all("loopback/link-local" in p.reason for p in props)


def test_own_radio_mac_and_broadcast_ssid_are_accepted():
    props = propose_lab_scope(LabFacts(
        macs=[("DC:A6:32:AA:BB:CC", "wlan0")],
        ssids=[("MyLabAP", "wlan1")],
    ))
    by = {p.kind: p for p in props}
    assert by["bssid"].value == "dc:a6:32:aa:bb:cc" and by["bssid"].accept is True
    assert by["ssid"].value == "MyLabAP" and by["ssid"].accept is True


def test_proposals_are_deduped():
    props = propose_lab_scope(LabFacts(
        cidrs=[("10.0.0.5/24", "eth0"), ("10.0.0.9/24", "eth1")],  # same network
    ))
    assert len(props) == 1 and props[0].value == "10.0.0.0/24"


# --- host parsers ------------------------------------------------------------ #

def test_parse_ip_json_skips_loopback_and_builds_cidrs():
    data = [
        {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
        {"ifname": "eth0", "addr_info": [
            {"family": "inet", "local": "192.168.1.10", "prefixlen": 24},
            {"family": "inet6", "local": "fe80::1", "prefixlen": 64},
        ]},
    ]
    out = parse_ip_json(data)
    assert ("192.168.1.10/24", "eth0") in out
    assert not any(iface == "lo" for _, iface in out)


def test_parse_iw_dev_takes_ap_ssid_not_managed_join():
    text = """phy#0
\tInterface wlan0
\t\taddr dc:a6:32:11:22:33
\t\tssid NeighborNet
\t\ttype managed
\tInterface wlan1
\t\taddr 00:c0:ca:44:55:66
\t\tssid MyLabAP
\t\ttype AP
"""
    macs, ssids = parse_iw_dev(text)
    assert ("dc:a6:32:11:22:33", "wlan0") in macs
    assert ("00:c0:ca:44:55:66", "wlan1") in macs
    # the managed interface's joined network must NOT be treated as ours
    assert ssids == [("MyLabAP", "wlan1")]


# --- collector: honest about absent tools ------------------------------------ #

def test_collect_lab_facts_records_missing_tools_without_raising():
    def broken(cmd):
        raise FileNotFoundError(cmd[0])
    facts = collect_lab_facts(runner=broken)
    assert facts.cidrs == [] and facts.macs == [] and facts.ssids == []
    assert len(facts.notes) == 2 and any("ip" in n for n in facts.notes)


def test_collect_lab_facts_with_injected_runner():
    def fake(cmd):
        if cmd[:2] == ["ip", "-json"]:
            return '[{"ifname":"eth0","addr_info":[{"family":"inet","local":"10.1.2.3","prefixlen":24}]}]'
        return "Interface wlan0\n\t\taddr aa:bb:cc:dd:ee:ff\n\t\ttype managed\n"
    facts = collect_lab_facts(runner=fake)
    assert ("10.1.2.3/24", "eth0") in facts.cidrs
    assert ("aa:bb:cc:dd:ee:ff", "wlan0") in facts.macs


# --- Scope.arm_lab_auto integration ------------------------------------------ #

def test_arm_lab_auto_arms_only_what_is_yours():
    facts = LabFacts(
        cidrs=[("192.168.50.7/24", "eth0"), ("8.8.8.9/24", "wwan0")],  # private + globally-routable
        macs=[("DC:A6:32:AA:BB:CC", "wlan0")],
        ssids=[("MyLabAP", "wlan1")],
    )
    scope = Scope()
    armed, proposals = scope.arm_lab_auto(facts)
    assert armed == 3                       # private cidr + mac + ssid; public skipped
    assert len(proposals) == 4

    # the private subnet authorizes a host in it; the public one does NOT
    assert scope.permits(ip="192.168.50.200") is True
    assert scope.permits(ip="8.8.8.50") is False
    assert scope.permits(bssid="dc:a6:32:aa:bb:cc") is True
    assert scope.permits(ssid="MyLabAP") is True
    # everything armed went under the 'lab' job
    assert all(e.job == "lab" for e in scope.active_entries())


def test_arm_lab_auto_on_empty_facts_arms_nothing():
    scope = Scope()
    armed, proposals = scope.arm_lab_auto(LabFacts())
    assert armed == 0 and proposals == []
    assert scope.empty is True
