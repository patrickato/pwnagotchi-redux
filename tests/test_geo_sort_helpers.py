from redux.geo import Sighting
from redux.geo.sort_helpers import sort_by_rssi, sort_by_ssid, sort_by_ts


def test_sorts():
    rows = [
        Sighting(kind="wifi", mac="a", ssid="Z", rssi=-80, ts=2.0, provenance="t"),
        Sighting(kind="wifi", mac="b", ssid="A", rssi=-40, ts=1.0, provenance="t"),
    ]
    assert sort_by_rssi(rows)[0].rssi == -40
    assert sort_by_ts(rows)[0].ts == 2.0
    assert sort_by_ssid(rows)[0].ssid == "A"
