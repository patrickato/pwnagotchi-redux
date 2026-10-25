"""Temporal RF diff — what changed at a location since last visit (pure engine)."""
from redux.geo.db import Sighting, SightingStore
from redux.geo.location_memory import (
    LocationMemory, rf_diff, location_key, devices_here,
)


def _store(rows):
    """rows: (kind, mac, ssid, channel, rssi, lat, lon)."""
    s = SightingStore(":memory:")
    for i, (k, m, ssid, ch, rssi, lat, lon) in enumerate(rows):
        s.insert(Sighting(kind=k, mac=m, ssid=ssid, channel=ch, rssi=rssi,
                          lat=lat, lon=lon, ts=100.0 + i, provenance="t"))
    return s


def test_first_visit_sets_baseline_without_fabricating(tmp_path):
    mem = LocationMemory(str(tmp_path / "m.json"))
    store = _store([("wifi", "aa:bb:cc:dd:ee:01", "Net", 6, -40, None, None)])
    d = rf_diff(store, mem, key="label:home")
    assert d.first_visit is True
    assert d.here_count == 1 and not d.new and not d.gone and not d.changed


def test_new_gone_and_changed_across_two_visits(tmp_path):
    path = str(tmp_path / "m.json")
    rf_diff(_store([("wifi", "aa:01", "Net", 6, -40, None, None),
                    ("ble", "bb:02", "", None, -60, None, None)]),
            LocationMemory(path), key="label:home")
    d = rf_diff(_store([("wifi", "aa:01", "Net", 11, -72, None, None),   # changed ch + rssi
                        ("wifi", "cc:03", "NewAP", 1, -50, None, None)]),  # new; bb:02 gone
                LocationMemory(path), key="label:home")
    assert d.first_visit is False
    assert {x.mac for x in d.new} == {"cc:03"}
    assert {x.mac for x in d.gone} == {"bb:02"}
    assert {c.mac for c in d.changed} == {"aa:01"}
    assert "channel 6 -> 11" in d.changed[0].why and "RSSI" in d.changed[0].why


def test_geohash_key_filters_to_devices_in_that_cell(tmp_path):
    store = _store([("wifi", "aa:01", "Here", 6, -40, 42.50, -83.10),
                    ("wifi", "bb:02", "FarAway", 6, -40, 10.00, 10.00)])
    key = location_key(lat=42.50, lon=-83.10)
    assert key.startswith("geo:")
    d = rf_diff(store, LocationMemory(str(tmp_path / "m.json")), key=key)
    assert d.here_count == 1               # the far-away device is not "here"


def test_corrupt_memory_file_is_treated_as_first_visit(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{ not valid json")
    d = rf_diff(_store([("wifi", "aa:01", "Net", 6, -40, None, None)]),
                LocationMemory(str(p)), key="label:home")
    assert d.first_visit is True           # fail-safe, no crash


def test_no_save_leaves_baseline_untouched(tmp_path):
    path = str(tmp_path / "m.json")
    rf_diff(_store([("wifi", "aa:01", "Net", 6, -40, None, None)]),
            LocationMemory(path), key="label:home")               # baseline: 1 device
    # a peek that must not overwrite the baseline
    rf_diff(_store([("wifi", "zz:99", "Other", 1, -50, None, None)]),
            LocationMemory(path), key="label:home", save=False)
    # next real visit still diffs against the ORIGINAL baseline (aa:01), not zz:99
    d = rf_diff(_store([("wifi", "aa:01", "Net", 6, -40, None, None)]),
                LocationMemory(path), key="label:home")
    assert not d.new and not d.gone        # unchanged vs the preserved baseline


def test_location_key_label_coords_and_none():
    assert location_key(label="the shop") == "label:the shop"
    assert location_key(lat=42.5, lon=-83.1).startswith("geo:")
    assert location_key() is None
    assert location_key(label="   ") is None


# --- CLI wiring (`redux here`) ---------------------------------------------- #

def test_cmd_here_first_visit_and_diff(tmp_path, capsys):
    from redux.cli import cmd_here
    from types import SimpleNamespace
    db = tmp_path / "s.db"
    s = SightingStore(str(db))
    s.insert(Sighting(kind="wifi", mac="aa:01", ssid="HomeNet", channel=6, rssi=-40, ts=1.0, provenance="t"))
    base = dict(label="shop", lat=None, lon=None, precision=7, rssi_delta=18,
                window_hours=None, db=str(db), memory=str(tmp_path / "m.json"),
                no_save=False, host="127.0.0.1", port=2947, timeout=0.2)
    assert cmd_here(SimpleNamespace(**base)) == 0
    assert "first visit" in capsys.readouterr().out
    # second visit after a new device arrives
    s.insert(Sighting(kind="ble", mac="bb:02", ssid="", channel=None, rssi=-55, ts=2.0, provenance="t"))
    assert cmd_here(SimpleNamespace(**base)) == 0
    out = capsys.readouterr().out
    assert "+1 new" in out and "NEW" in out and "bb:02" in out


def test_cmd_here_without_location_is_honest(tmp_path, capsys):
    from redux.cli import cmd_here
    from types import SimpleNamespace
    args = SimpleNamespace(label=None, lat=None, lon=None, precision=7, rssi_delta=18,
                           window_hours=None, db=str(tmp_path / "s.db"),
                           memory=str(tmp_path / "m.json"), no_save=False,
                           host="127.0.0.1", port=2947, timeout=0.2)
    assert cmd_here(args) == 2
    assert "no location" in capsys.readouterr().out.lower()
