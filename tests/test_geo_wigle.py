"""Hardware-free tests for WiGLE CSV export (Grok geo #2)."""
from __future__ import annotations

import csv
import io

from redux.geo import (
    WIGLE_COLUMNS,
    Sighting,
    SightingStore,
    channel_to_frequency_mhz,
    export_store,
    kismetdb_to_wiglecsv_dump,
    write_wigle_csv,
)


def _wifi(**kw) -> Sighting:
    base = dict(
        kind="wifi",
        mac="aa:bb:cc:dd:ee:01",
        ssid="CafeNet",
        lat=37.7749,
        lon=-122.4194,
        rssi=-65,
        channel=6,
        source_radio="wlan0",
        ts=1_700_000_000.0,
        provenance="test wifi sighting",
    )
    base.update(kw)
    return Sighting(**base)


def _ble(**kw) -> Sighting:
    base = dict(
        kind="ble",
        mac="11:22:33:44:55:66",
        ssid="",
        lat=37.7750,
        lon=-122.4195,
        rssi=-72,
        channel=None,
        source_radio="hci0",
        ts=1_700_000_100.0,
        provenance="test ble sighting",
    )
    base.update(kw)
    return Sighting(**base)


def test_channel_to_frequency_24ghz():
    assert channel_to_frequency_mhz(1) == 2412
    assert channel_to_frequency_mhz(6) == 2437
    assert channel_to_frequency_mhz(11) == 2462
    assert channel_to_frequency_mhz(14) == 2484


def test_channel_to_frequency_5ghz():
    assert channel_to_frequency_mhz(36) == 5180
    assert channel_to_frequency_mhz(149) == 5745


def test_write_wigle_csv_headers_and_rows():
    buf = io.StringIO()
    n = write_wigle_csv([_wifi(), _ble()], buf)
    assert n == 2
    text = buf.getvalue()
    lines = text.strip().splitlines()
    assert lines[0].startswith("WigleWifi-1.6,")
    assert "appRelease=" in lines[0]
    assert lines[1] == ",".join(WIGLE_COLUMNS)

    reader = csv.reader(io.StringIO("\n".join(lines[1:])))
    header = next(reader)
    assert header == WIGLE_COLUMNS
    rows = list(reader)
    assert len(rows) == 2
    assert rows[0][0] == "AA:BB:CC:DD:EE:01"
    assert rows[0][1] == "CafeNet"
    assert rows[0][4] == "6"
    assert rows[0][5] == "2437"
    assert rows[0][-1] == "WIFI"
    assert rows[1][-1] == "BLE"
    assert rows[1][2] == "[LE]"


def test_export_store_round_trip():
    with SightingStore(":memory:") as db:
        db.insert(_wifi())
        db.insert(_ble())
        buf = io.StringIO()
        n = export_store(db, buf)
        assert n == 2
        assert "WigleWifi-1.6" in buf.getvalue()
        assert "AA:BB:CC:DD:EE:01" in buf.getvalue()


def test_kismetdb_style_dump_string():
    text = kismetdb_to_wiglecsv_dump([_wifi(ssid="Comma,Net")])
    assert text.startswith("WigleWifi-1.6,")
    # CSV quotes fields with commas
    assert '"Comma,Net"' in text or "Comma,Net" in text
    assert "WIFI" in text


def test_missing_gps_emits_empty_fields():
    buf = io.StringIO()
    write_wigle_csv([_wifi(lat=None, lon=None)], buf)
    rows = list(csv.reader(io.StringIO(buf.getvalue())))
    data = rows[2]
    # CurrentLatitude / CurrentLongitude empty
    assert data[7] == ""
    assert data[8] == ""
