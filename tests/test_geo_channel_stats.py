from redux.geo import Sighting, SightingStore
from redux.geo.channel_stats import busiest_channels, channel_summary


def test_busiest():
    with SightingStore(":memory:") as db:
        for i in range(3):
            db.insert(Sighting(kind="wifi", mac=f"aa:aa:aa:aa:aa:0{i}", channel=6, ts=float(i), provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", channel=1, ts=9.0, provenance="t"))
        top = busiest_channels(db)
        assert top[0] == (6, 3)
        s = channel_summary(db)
        assert "channel stats" in s["reason"]
