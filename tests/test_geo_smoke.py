from redux.geo.smoke import smoke_roundtrip


def test_smoke_ok():
    r = smoke_roundtrip()
    assert r["ok"] is True
    assert r["rssi"] == -40
    assert "smoke ok" in r["reason"]
