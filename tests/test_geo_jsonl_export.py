from pathlib import Path

from redux.geo import Sighting, SightingStore
from redux.geo.jsonl_export import export_store_jsonl, to_jsonl


def test_jsonl(tmp_path: Path):
    s = Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t")
    assert "aa:aa:aa:aa:aa:01" in to_jsonl([s])
    with SightingStore(":memory:") as db:
        db.insert(s)
        n = export_store_jsonl(db, tmp_path / "out.jsonl")
        assert n == 1
