"""Field Dex — recon ledger over the sightings store."""
from redux.dex import build_dex, Rarity
from redux.geo import SightingStore, Sighting


def _store():
    s = SightingStore(":memory:")
    # three devices sharing OUI aa:bb:cc -> that vendor is "rare" (count 3)
    for i in range(3):
        s.insert(Sighting(kind="wifi", mac=f"aa:bb:cc:00:00:{i:02x}", ssid=f"Net{i}",
                          ts=1000.0 + i, first_seen=1000.0 + i, provenance="t"))
    # a one-off vendor -> legendary
    s.insert(Sighting(kind="wifi", mac="11:22:33:44:55:66", ssid="Unique",
                      ts=1005.0, first_seen=1005.0, provenance="t"))
    return s


def test_rarity_from_own_vendor_frequency():
    dex = build_dex(_store(), now=1005.0)
    by_mac = {e.mac: e for e in dex.entries}
    assert by_mac["aa:bb:cc:00:00:00"].rarity is Rarity.RARE        # OUI appears 3x
    assert by_mac["11:22:33:44:55:66"].rarity is Rarity.LEGENDARY   # one-off OUI
    assert dex.summary["total"] == 4
    assert dex.summary["unique_vendors"] == 2


def test_known_days_and_located():
    s = SightingStore(":memory:")
    s.insert(Sighting(kind="wifi", mac="aa:bb:cc:dd:ee:ff", ssid="Home",
                      first_seen=0.0, ts=10 * 86400.0, lat=40.1, lon=-80.2, provenance="t"))
    e = build_dex(s, now=10 * 86400.0).entries[0]
    assert round(e.known_days) == 10 and e.located is True


def test_departed_flag_for_long_quiet_device():
    s = SightingStore(":memory:")
    # old device: last seen 20 days before the reference
    s.insert(Sighting(kind="wifi", mac="de:ad:be:ef:00:01", ssid="Corner",
                      first_seen=0.0, ts=0.0, provenance="t"))
    # recent device sets the reference "now"
    s.insert(Sighting(kind="wifi", mac="de:ad:be:ef:00:02", ssid="Fresh",
                      first_seen=20 * 86400.0, ts=20 * 86400.0, provenance="t"))
    dex = build_dex(s, departed_after_days=7.0)   # now defaults to latest ts (20d)
    departed = {e.mac for e in dex.departed()}
    assert "de:ad:be:ef:00:01" in departed
    assert "de:ad:be:ef:00:02" not in departed
    gone = next(e for e in dex.entries if e.mac == "de:ad:be:ef:00:01")
    assert "departed" in gone.reason and "Corner" in gone.reason


def test_rarest_orders_by_rarity():
    dex = build_dex(_store(), now=1005.0)
    rarest = dex.rarest(limit=1)
    assert rarest[0].rarity is Rarity.LEGENDARY


def test_empty_store_is_empty_dex():
    dex = build_dex(SightingStore(":memory:"))
    assert dex.summary["total"] == 0 and dex.entries == []
