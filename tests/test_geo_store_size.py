from pathlib import Path

from redux.geo import Sighting, SightingStore
from redux.geo.store_size import store_file_size, store_size_summary


def test_memory_is_none():
    with SightingStore(":memory:") as db:
        assert store_file_size(db) is None


def test_file_size(tmp_path: Path):
    path = str(tmp_path / "s.db")
    with SightingStore(path) as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        assert store_file_size(db) is not None and store_file_size(db) > 0
        assert "bytes" in store_size_summary(db)["reason"]
