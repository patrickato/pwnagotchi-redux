"""Temporal RF diff × reid: MAC randomization no longer fools 'what changed here'.

A phone that leaves and returns under a fresh random MAC used to read as a
GONE + NEW pair. Resolving devices through the probe-PNL entity map (reid)
recognises it as the same device.
"""
from types import SimpleNamespace

from redux.geo.db import Sighting, SightingStore
from redux.geo.location_memory import LocationMemory, rf_diff, build_entity_map

# two randomized (locally-administered) MACs of ONE phone, rotated between visits
MAC_A = "fa:11:22:33:44:01"
MAC_B = "fe:55:66:77:88:02"
_PNL = [
    {"client": MAC_A, "ssids": ["Bob-iPhone", "HomeNet"], "probes": 5},
    {"client": MAC_B, "ssids": ["Bob-iPhone", "CoffeeShop"], "probes": 4},
]


def _store(rows):
    s = SightingStore(":memory:")
    for i, (k, m, ssid, ch, rssi) in enumerate(rows):
        s.insert(Sighting(kind=k, mac=m, ssid=ssid, channel=ch, rssi=rssi, ts=100.0 + i, provenance="t"))
    return s


def test_build_entity_map_groups_rotated_macs():
    em = build_entity_map(_PNL)
    assert em[MAC_A] == em[MAC_B]            # one physical device
    assert em[MAC_A].startswith("entity:")


def test_without_entities_a_rotated_mac_is_a_false_gone_and_new(tmp_path):
    path = str(tmp_path / "m.json")
    rf_diff(_store([("wifi", MAC_A, "Net", 6, -40)]), LocationMemory(path), key="label:shop")
    d = rf_diff(_store([("wifi", MAC_B, "Net", 6, -40)]), LocationMemory(path), key="label:shop")
    assert {x.mac for x in d.new} == {MAC_B}
    assert {x.mac for x in d.gone} == {MAC_A}      # the blind spot: same phone, counted twice


def test_with_entities_the_rotated_device_is_recognized(tmp_path):
    path = str(tmp_path / "m.json")
    em = build_entity_map(_PNL)
    rf_diff(_store([("wifi", MAC_A, "Net", 6, -40)]), LocationMemory(path), key="label:shop", entities=em)
    d = rf_diff(_store([("wifi", MAC_B, "Net", 6, -40)]), LocationMemory(path), key="label:shop", entities=em)
    assert not d.new and not d.gone                # recognised as the same device returning


def test_entities_do_not_merge_genuinely_different_devices(tmp_path):
    # a real newcomer (not in any entity) is still flagged NEW
    path = str(tmp_path / "m.json")
    em = build_entity_map(_PNL)
    rf_diff(_store([("wifi", MAC_A, "Net", 6, -40)]), LocationMemory(path), key="label:shop", entities=em)
    d = rf_diff(_store([("wifi", MAC_A, "Net", 6, -40),
                        ("wifi", "3c:00:00:00:00:99", "Other", 1, -55)]),   # unrelated device
                LocationMemory(path), key="label:shop", entities=em)
    assert {x.mac for x in d.new} == {"3c:00:00:00:00:99"} and not d.gone


# --- CLI (`redux here --pnl`) ---------------------------------------------- #

def _ns(**kw):
    base = dict(label="shop", lat=None, lon=None, precision=7, rssi_delta=18,
                window_hours=None, db=None, memory=None, pnl=None, no_save=False,
                host="127.0.0.1", port=2947, timeout=0.2)
    base.update(kw)
    return SimpleNamespace(**base)


def test_cmd_here_pnl_derandomizes(tmp_path, capsys):
    from redux.cli import cmd_here
    from redux.portal.karma import ProbeHarvester
    # visit 1 store (MAC_A) and visit 2 store (MAC_B) — the phone rotated
    db1 = tmp_path / "v1.db"; db2 = tmp_path / "v2.db"
    SightingStore(str(db1)).insert(Sighting(kind="wifi", mac=MAC_A, ssid="Net", channel=6, rssi=-40, ts=1.0, provenance="t"))
    SightingStore(str(db2)).insert(Sighting(kind="wifi", mac=MAC_B, ssid="Net", channel=6, rssi=-40, ts=2.0, provenance="t"))
    pnl = tmp_path / "pnl.json"
    h = ProbeHarvester(path=str(pnl))
    for ssid in ("Bob-iPhone", "HomeNet"):
        h.note(MAC_A, ssid)
    for ssid in ("Bob-iPhone", "CoffeeShop"):
        h.note(MAC_B, ssid)
    mem = str(tmp_path / "m.json")
    # visit 1: baseline
    assert cmd_here(_ns(db=str(db1), memory=mem)) == 0
    capsys.readouterr()
    # visit 2 WITHOUT pnl -> false NEW/GONE
    assert cmd_here(_ns(db=str(db2), memory=mem, no_save=True)) == 0
    assert "NEW" in capsys.readouterr().out
    # visit 2 WITH pnl -> de-randomized, no spurious NEW
    assert cmd_here(_ns(db=str(db2), memory=mem, pnl=str(pnl), no_save=True)) == 0
    out = capsys.readouterr().out
    assert "de-randomized" in out and "NEW" not in out
