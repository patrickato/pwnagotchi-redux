import threading
from pathlib import Path

from redux.geo import Sighting
from redux.geo.locked_store import LockedSightingStore


def test_basic_ops():
    with LockedSightingStore(path=":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        assert db.count() == 1
        assert db.get("wifi", "aa:aa:aa:aa:aa:01") is not None


def test_concurrent_inserts(tmp_path: Path):
    path = str(tmp_path / "sightings.db")
    db = LockedSightingStore(path=path)

    def worker(start: int) -> None:
        for i in range(20):
            mac = f"aa:aa:aa:aa:{start:02x}:{i:02x}"
            db.insert(Sighting(kind="wifi", mac=mac, ts=float(i), provenance="t"))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert db.count() == 80
    db.close()
