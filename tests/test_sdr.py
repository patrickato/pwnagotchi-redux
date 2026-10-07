"""SDR passive-sensing ingest — rtl_433 ISM + ADS-B aircraft → geo-tagged Dex."""
from redux.sdr import rtl433_to_sighting, adsb_to_sighting, ingest_rtl433, ingest_adsb
from redux.geo import SightingStore
from redux.dex import build_dex


def test_rtl433_record_becomes_ism_sighting_tagged_with_receiver_position():
    rec = {"time": "2026-10-07 15:00:00", "model": "Acurite-Tower", "id": 1234,
           "channel": "A", "temperature_C": 21.5}
    s = rtl433_to_sighting(rec, position=(40.1, -80.2))
    assert s is not None and s.kind == "ism"
    assert s.ssid == "Acurite-Tower" and "acurite-tower" in s.mac and "1234" in s.mac
    assert s.lat == 40.1 and s.lon == -80.2          # tagged with where WE heard it
    assert s.ts == 1791385200.0                       # 2026-10-07 15:00:00 UTC
    assert s.provenance == "rtl_433"


def test_rtl433_without_id_or_model_is_skipped():
    assert rtl433_to_sighting({"temperature_C": 20.0}) is None


def test_adsb_record_uses_aircraft_own_position():
    rec = {"hex": "A1B2C3", "flight": "UAL123 ", "lat": 41.5, "lon": -81.7, "now": 1759849200.0}
    s = adsb_to_sighting(rec)
    assert s is not None and s.kind == "adsb"
    assert s.mac == "a1b2c3" and s.ssid == "UAL123"
    assert s.lat == 41.5 and s.lon == -81.7          # the plane's own broadcast position
    assert s.provenance == "dump1090"


def test_adsb_without_icao_is_skipped():
    assert adsb_to_sighting({"flight": "GHOST"}) is None


def test_ingest_batches_and_shows_up_in_the_dex():
    store = SightingStore(":memory:")
    ism = [{"time": "2026-10-07 15:00:00", "model": "LaCrosse-TX", "id": i} for i in range(3)]
    air = [{"hex": f"a0000{i}", "flight": f"FL{i}", "lat": 40.0 + i, "lon": -80.0, "now": 1759849200.0}
           for i in range(2)]
    assert ingest_rtl433(ism, store, position=(40.0, -80.0)) == 3
    assert ingest_adsb(air, store) == 2
    dex = build_dex(store)
    assert dex.summary["by_kind"].get("ism") == 3
    assert dex.summary["by_kind"].get("adsb") == 2
    assert dex.summary["located"] == 5               # ISM tagged by receiver, ADS-B by own pos


def test_empty_ingest_writes_nothing():
    store = SightingStore(":memory:")
    assert ingest_rtl433([], store) == 0 and ingest_adsb([{"flight": "x"}], store) == 0
    assert store.count() == 0
