"""Hardware-free tests for WiGLE CSV import (Grok GG6)."""
from redux.geo import SightingStore
from redux.geo.wigle_import import import_wigle_csv

CSV = """WigleWifi-1.6,appRelease=1,model=x,release=1,device=x,display=x,board=x,brand=x
MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type
AA:BB:CC:DD:EE:01,Cafe,[WPA2-PSK-CCMP],2020-01-01 00:00:00,6,-55,37.5,-122.2,0,10,WIFI
"""


def test_import_one_row():
    with SightingStore(":memory:") as store:
        n = import_wigle_csv(CSV, store)
        assert n == 1
        row = store.get("wifi", "aa:bb:cc:dd:ee:01")
        assert row is not None
        assert row.ssid == "Cafe"
        assert row.lat == 37.5
