from redux.geo import Sighting
from redux.geo.dedup_report import dedup_report


def test_dedup_report():
    rows = [
        Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"),
        Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=2.0, provenance="t"),
        Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=3.0, provenance="t"),
    ]
    r = dedup_report(rows)
    assert r.unique == 2 and r.duplicates == 1
    assert "dedup report" in r.reason
