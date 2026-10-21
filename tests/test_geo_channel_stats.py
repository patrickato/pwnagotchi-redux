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

def test_channel_counts_sql_bounded_and_kind_filtered(monkeypatch):
    from redux.geo.channel_stats import channel_counts, channel_summary
    with SightingStore(":memory:") as db:
        rows = [Sighting(kind="wifi", mac=f"{i:012x}", channel=6 if i % 2 else 11,
                         rssi=-45, provenance="test") for i in range(2400)]
        rows.append(Sighting(kind="ble", mac="aa:bb:cc:dd:ee:ff",
                             channel=6, provenance="test"))
        db.insert_many(rows)
        # SQL aggregation must not call query() without a limit and load 2401 rows.
        def unsafe_query(*args, **kwargs):
            raise AssertionError("full-table Python materialization forbidden")
        monkeypatch.setattr(db, "query", unsafe_query)
        assert channel_counts(db) == {6: 1201, 11: 1200}
        assert channel_counts(db, kind="wifi") == {6: 1200, 11: 1200}
        assert channel_summary(db)["total_with_channel"] == 2401
