from pathlib import Path

from redux.geo import Sighting, SightingStore
from redux.geo.backup import dump_store_json, restore_store_json


def test_roundtrip(tmp_path: Path):
    path = tmp_path / "bak.json"
    with SightingStore(":memory:") as src:
        src.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ssid="X", lat=1.0, lon=2.0, ts=5.0, provenance="t"))
        assert dump_store_json(src, path) == 1
    with SightingStore(":memory:") as dst:
        assert restore_store_json(dst, path) == 1
        row = dst.get("wifi", "aa:aa:aa:aa:aa:01")
        assert row is not None and row.ssid == "X"
