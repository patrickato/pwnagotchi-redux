from redux.geo import SightingStore
from redux.geo.jsonl_import import import_jsonl


def test_import():
    line = '{"kind":"wifi","mac":"AA:BB:CC:DD:EE:01","ssid":"X","ts":1.0,"provenance":"t"}\n'
    with SightingStore(":memory:") as db:
        assert import_jsonl(line, db) == 1
        assert db.get("wifi", "aa:bb:cc:dd:ee:01") is not None
