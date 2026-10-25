"""reid device dossier — entity × sightings × OUI (feed fusion)."""
from types import SimpleNamespace

from redux.geo.reid import reidentify, enrich_entities, Dossier
from redux.geo.db import Sighting, SightingStore

# two randomized MACs of one phone + a real (OUI-assigned) laptop MAC
FA = "fa:11:22:33:44:01"
FE = "fe:55:66:77:88:02"
_PNL = [
    {"client": FA, "ssids": ["Bob-iPhone", "HomeNet"], "probes": 5},
    {"client": FE, "ssids": ["Bob-iPhone", "CoffeeShop"], "probes": 4},
]


def _by_mac(rows):
    d = {}
    for s in rows:
        d.setdefault(s.mac.lower(), []).append(s)
    return d


def test_dossier_joins_sightings_and_summarizes():
    ents = reidentify(_PNL)
    assert ents, "precondition: the two MACs link into one entity"
    rows = [Sighting(kind="wifi", mac=FA, ssid="", rssi=-60, lat=42.5, lon=-83.1, ts=100.0, provenance="t"),
            Sighting(kind="wifi", mac=FE, ssid="", rssi=-42, lat=42.5, lon=-83.1, ts=200.0, provenance="t")]
    dossiers = enrich_entities(ents, _by_mac(rows))
    d = dossiers[0]
    assert d.sightings == 2
    assert d.last_seen == 200.0 and d.strongest_rssi == -42     # strongest across both MACs
    assert len(d.places) == 1                                   # same geohash cell


def test_randomized_only_entity_has_no_vendor():
    ents = reidentify(_PNL)
    d = enrich_entities(ents, {}, vendor_lookup=lambda m: "ShouldNotBeUsed")[0]
    assert d.vendor is None                                     # both MACs are randomized -> OUI skipped
    assert "randomized" in d.summary and d.sightings == 0


def test_vendor_resolved_from_a_non_randomized_member():
    # craft an entity containing one real OUI MAC by linking it to a randomized one
    pnl = [{"client": "3c:11:22:33:44:55", "ssids": ["Bob-iPhone", "X"], "probes": 3},
           {"client": FA, "ssids": ["Bob-iPhone", "Y"], "probes": 3}]
    ents = reidentify(pnl)
    assert ents and len(ents[0].macs) == 2
    d = enrich_entities(ents, {}, vendor_lookup=lambda m: "AcmeCo" if m.startswith("3c") else None)[0]
    assert d.vendor == "AcmeCo"                                 # from the globally-administered MAC


def test_no_sightings_is_honest():
    ents = reidentify(_PNL)
    d = enrich_entities(ents, {})[0]
    assert d.sightings == 0 and "probe-only" in d.summary


# --- CLI -------------------------------------------------------------------- #

def test_cmd_reid_shows_dossier_with_db(tmp_path, capsys):
    from redux.cli import cmd_reid
    from redux.portal.karma import ProbeHarvester
    import json as _json
    pnl = tmp_path / "pnl.json"
    h = ProbeHarvester(path=str(pnl))
    for s in ("Bob-iPhone", "HomeNet"):
        h.note(FA, s)
    for s in ("Bob-iPhone", "CoffeeShop"):
        h.note(FE, s)
    db = tmp_path / "s.db"
    store = SightingStore(str(db))
    store.insert(Sighting(kind="wifi", mac=FA, ssid="", rssi=-55, ts=10.0, provenance="t"))
    store.insert(Sighting(kind="wifi", mac=FE, ssid="", rssi=-44, ts=20.0, provenance="t"))
    args = SimpleNamespace(store=str(pnl), min_weight=0.5, max_common_fraction=0.5,
                           db=str(db), precision=7, json=False)
    assert cmd_reid(args) == 0
    out = capsys.readouterr().out
    assert "dossier:" in out and "seen 2" in out
    # JSON carries the dossier too
    args.json = True
    assert cmd_reid(args) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload[0]["dossier"]["sightings"] == 2 and payload[0]["dossier"]["strongest_rssi"] == -44
