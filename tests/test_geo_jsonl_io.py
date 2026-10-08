from pathlib import Path

from redux.geo import Sighting, SightingStore
from redux.geo.jsonl_io import export_jsonl, import_jsonl


def test_roundtrip(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    with SightingStore(":memory:") as src:
        src.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ssid="X", ts=1.0, provenance="t"))
        assert export_jsonl(src, path) == 1
    with SightingStore(":memory:") as dst:
        assert import_jsonl(dst, path) == 1
        assert dst.get("wifi", "aa:aa:aa:aa:aa:01") is not None
