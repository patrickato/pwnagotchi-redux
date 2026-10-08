from redux.geo import Sighting
from redux.geo.merge_rssi import merge_best_rssi


def test_keeps_stronger():
    rows = [
        Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", rssi=-80, ts=1.0, provenance="t"),
        Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", rssi=-40, ts=2.0, provenance="t"),
    ]
    out = merge_best_rssi(rows)
    assert len(out) == 1 and out[0].rssi == -40
