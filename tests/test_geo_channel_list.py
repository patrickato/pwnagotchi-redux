from redux.geo import Sighting, SightingStore
from redux.geo.channel_list import list_channels


def test_list_channels():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", channel=6, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", channel=1, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="cc:cc:cc:cc:cc:01", channel=6, ts=1.0, provenance="t"))
        assert list_channels(db) == [1, 6]
