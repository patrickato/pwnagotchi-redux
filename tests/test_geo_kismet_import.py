"""Hardware-free tests for kismetdb import (Grok G6)."""
import sqlite3
from pathlib import Path

from redux.geo import SightingStore
from redux.geo.kismet_import import import_kismetdb


def test_import_minimal_devices(tmp_path: Path):
    dbfile = tmp_path / "test.kismet"
    conn = sqlite3.connect(str(dbfile))
    conn.execute(
        "CREATE TABLE devices (devmac TEXT, name TEXT, avg_lat REAL, avg_lon REAL, strongest INT, last_time REAL)"
    )
    conn.execute(
        "INSERT INTO devices VALUES ('AA:BB:CC:DD:EE:01','Cafe',37.5,-122.2,-55,1000.0)"
    )
    conn.commit()
    conn.close()
    with SightingStore(":memory:") as store:
        n = import_kismetdb(dbfile, store)
        assert n == 1
        row = store.get("wifi", "aa:bb:cc:dd:ee:01")
        assert row is not None
        assert row.ssid == "Cafe"
        assert "kismetdb" in row.provenance
