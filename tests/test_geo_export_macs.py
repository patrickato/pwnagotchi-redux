from redux.geo import Sighting, SightingStore
from redux.geo.export_macs import export_macs_text, list_macs


def test_export_macs():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        macs = list_macs(db)
        assert macs[0].startswith("aa")
        text = export_macs_text(db)
        assert "aa:aa:aa:aa:aa:01" in text
