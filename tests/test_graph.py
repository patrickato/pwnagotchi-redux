"""Entity graph — reid nodes + fusion travels-with edges + annotations."""
from types import SimpleNamespace

from redux.intel.graph import build_graph, EntityNode, EntityEdge
from redux.geo.reid import reidentify
from redux.intel.fusion import fuse

FA = "fa:11:22:33:44:01"      # randomized phone, two MACs via shared PNL
FE = "fe:55:66:77:88:02"
BLE = "aa:00:00:00:00:99"     # a BLE device that travels with the phone
_PNL = [{"client": FA, "ssids": ["Bob-iPhone", "HomeNet"], "probes": 5},
        {"client": FE, "ssids": ["Bob-iPhone", "CoffeeShop"], "probes": 4}]


def _s(mac, ts, rssi=None, lat=None, lon=None):
    return SimpleNamespace(mac=mac, ts=ts, rssi=rssi, lat=lat, lon=lon, ssid="", kind="wifi")


def _obs(mac, ts, lat=42.5, lon=-83.1):
    return SimpleNamespace(mac=mac, ts=ts, lat=lat, lon=lon)


def test_reid_merges_macs_into_one_device_node():
    ents = reidentify(_PNL)
    g = build_graph(reid_entities=ents, sightings_by_mac={FA.lower(): [_s(FA, 100.0, -40)],
                                                          FE.lower(): [_s(FE, 200.0, -50)]})
    phone = [n for n in g.nodes if FA in n.macs][0]
    assert set(phone.macs) == {FA, FE}          # two randomized MACs = one device
    assert phone.randomized == 2 and phone.sightings == 2
    assert phone.last_seen == 200.0 and phone.linked_by == ("pnl",)


def test_fusion_becomes_travels_with_edge_not_a_merge():
    ents = reidentify(_PNL)
    # phone (FA) and a BLE device co-travel across windows -> association edge
    obs = []
    for w in range(4):
        obs += [_obs(FA, w * 120), _obs(BLE, w * 120)]
    groups = fuse(obs, min_co=2)
    g = build_graph(reid_entities=ents, fusion_groups=groups,
                    sightings_by_mac={FA.lower(): [_s(FA, 1.0)], BLE.lower(): [_s(BLE, 2.0)]})
    # FA's node and BLE's node stay SEPARATE (association != identity) but linked by an edge
    node_ids = {n.id for n in g.nodes}
    assert "dev:" + BLE in node_ids                  # BLE is its own node, not merged into the phone
    assert any(e.relation == "travels-with" for e in g.edges)


def test_cracked_key_flag_from_loot():
    ents = reidentify(_PNL)
    g = build_graph(reid_entities=ents, cracked_macs={FA})
    phone = [n for n in g.nodes if FA in n.macs][0]
    assert phone.cracked is True and "KEY RECOVERED" in phone.summary


def test_vendor_only_from_non_randomized_member():
    pnl = [{"client": "3c:11:22:33:44:55", "ssids": ["Bob-iPhone", "X"], "probes": 3},
           {"client": FA, "ssids": ["Bob-iPhone", "Y"], "probes": 3}]
    ents = reidentify(pnl)
    g = build_graph(reid_entities=ents, vendor_lookup=lambda m: "AcmeCo" if m.startswith("3c") else None)
    assert g.nodes[0].vendor == "AcmeCo"


def test_empty_graph_is_empty():
    g = build_graph()
    assert g.nodes == () and g.edges == () and "0 device" in g.summary


# --- CLI -------------------------------------------------------------------- #

def test_cmd_graph_human_and_json(tmp_path, capsys):
    from redux.cli import cmd_graph
    from redux.portal.karma import ProbeHarvester
    from redux.geo.db import SightingStore, Sighting
    from redux.geo.follower import ObservationLog
    import json as _json
    pnl = tmp_path / "pnl.json"
    h = ProbeHarvester(path=str(pnl))
    for s in ("Bob-iPhone", "HomeNet"):
        h.note(FA, s)
    for s in ("Bob-iPhone", "CoffeeShop"):
        h.note(FE, s)
    db = tmp_path / "s.db"
    st = SightingStore(str(db))
    st.insert(Sighting(kind="wifi", mac=FA, ssid="", rssi=-40, ts=10.0, provenance="t"))
    st.insert(Sighting(kind="wifi", mac=FE, ssid="", rssi=-44, ts=20.0, provenance="t"))
    obs = tmp_path / "obs.jsonl"
    ol = ObservationLog(str(obs))
    for w in range(3):
        ol.record(FA, w * 120.0, lat=42.5, lon=-83.1)
        ol.record(BLE, w * 120.0, lat=42.5, lon=-83.1)
    args = SimpleNamespace(pnl=str(pnl), obs=str(obs), db=str(db),
                           loot=str(tmp_path / "none.json"), json=False)
    assert cmd_graph(args) == 0
    out = capsys.readouterr().out
    assert "ENTITY GRAPH" in out and "travels-with" in out
    args.json = True
    assert cmd_graph(args) == 0
    payload = _json.loads(capsys.readouterr().out)
    phone = [n for n in payload["nodes"] if FA in n["macs"]][0]
    assert set(phone["macs"]) == {FA, FE} and payload["edges"]
