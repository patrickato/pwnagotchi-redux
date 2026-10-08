from redux.geo import Sighting
from redux.geo.validate import filter_valid, validate_sighting


def test_ok():
    s = Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=1.0, lon=2.0, rssi=-50, ts=1.0, provenance="t")
    ok, reason = validate_sighting(s)
    assert ok and reason.startswith("ok")


def test_bad_lat():
    s = Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=999.0, ts=1.0, provenance="t")
    ok, reason = validate_sighting(s)
    assert not ok and "lat" in reason


def test_empty_mac():
    s = Sighting(kind="wifi", mac="", ts=1.0, provenance="t")
    assert validate_sighting(s)[0] is False


def test_filter_valid():
    good = Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t")
    bad = Sighting(kind="wifi", mac="", ts=1.0, provenance="t")
    g, reasons = filter_valid([good, bad])
    assert len(g) == 1 and len(reasons) == 1
