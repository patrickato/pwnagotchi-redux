"""Airwave DVR — scrub back through RF history."""
from redux.radio.airwave_dvr import AirwaveDVR, AirwaveEvent, band_of_channel


def _ev(ts, kind="beacon", bssid="", ssid="", channel=6, rssi=-50):
    return AirwaveEvent(ts=ts, kind=kind, bssid=bssid, ssid=ssid, channel=channel, rssi=rssi)


def test_band_of_channel():
    assert band_of_channel(6) == "2.4" and band_of_channel(11) == "2.4"
    assert band_of_channel(36) == "5" and band_of_channel(149) == "5"
    assert band_of_channel(None) == "?"


def test_record_and_span():
    dvr = AirwaveDVR()
    dvr.record(_ev(100.0, bssid="aa:bb:cc:dd:ee:01"))
    dvr.record(_ev(160.0, bssid="aa:bb:cc:dd:ee:02"))
    assert dvr.span() == (100.0, 160.0)
    assert dvr.summary()["events"] == 2 and dvr.summary()["unique_aps"] == 2


def test_scrub_snapshots_the_air_at_an_instant():
    dvr = AirwaveDVR()
    dvr.record(_ev(100.0, kind="beacon", bssid="aa:00:00:00:00:01", ssid="Home", channel=6))
    dvr.record(_ev(101.0, kind="deauth", bssid="aa:00:00:00:00:01", channel=6))
    dvr.record(_ev(500.0, kind="beacon", bssid="bb:00:00:00:00:02", ssid="Far", channel=36))
    snap = dvr.scrub(100.5, radius=5.0)
    assert snap["events"] == 2                       # the two near t=100, not the far one
    assert snap["aps"] == ["aa:00:00:00:00:01"]
    assert snap["kinds"] == {"beacon": 1, "deauth": 1}
    assert snap["bands"] == ["2.4"]


def test_window_replays_a_slice_in_order():
    dvr = AirwaveDVR()
    for t in (300.0, 100.0, 200.0):
        dvr.record(_ev(t, bssid="aa:00:00:00:00:0" + str(int(t // 100))))
    evs = dvr.window(100.0, 250.0)
    assert [e.ts for e in evs] == [100.0, 200.0]     # sorted, 300 excluded


def test_timeline_buckets_activity():
    dvr = AirwaveDVR()
    for t in (0.0, 10.0, 20.0):
        dvr.record(_ev(t, kind="beacon"))
    dvr.record(_ev(70.0, kind="deauth"))
    rows = dvr.timeline(bucket_seconds=60.0)
    assert rows[0]["t"] == 0.0 and rows[0]["count"] == 3 and rows[0]["top_kind"] == "beacon"
    assert rows[1]["t"] == 60.0 and rows[1]["count"] == 1 and rows[1]["top_kind"] == "deauth"


def test_track_follows_one_bssid_over_time():
    dvr = AirwaveDVR()
    dvr.record(_ev(10.0, bssid="aa:bb:cc:dd:ee:ff"))
    dvr.record(_ev(90.0, bssid="aa:bb:cc:dd:ee:ff"))
    dvr.record(_ev(50.0, bssid="11:22:33:44:55:66"))
    hist = dvr.track("AA:BB:CC:DD:EE:FF")
    assert [e.ts for e in hist] == [10.0, 90.0]


def test_age_and_count_bounds_trim():
    dvr = AirwaveDVR(max_age_seconds=100.0, max_events=10)
    dvr.record(_ev(0.0))
    dvr.record(_ev(500.0))                            # now=500 → the t=0 event ages out
    assert dvr.summary()["events"] == 1
    dvr2 = AirwaveDVR(max_age_seconds=1e9, max_events=3)
    for t in range(6):
        dvr2.record(_ev(float(t)))
    assert dvr2.summary()["events"] == 3              # capped to the last 3


def test_jsonl_persistence_roundtrip(tmp_path):
    p = str(tmp_path / "airwave.jsonl")
    dvr = AirwaveDVR(path=p)
    dvr.record(_ev(100.0, kind="pmkid", bssid="aa:bb:cc:dd:ee:01", ssid="Net", channel=11))
    dvr2 = AirwaveDVR(path=p)
    assert dvr2.load() == 1
    assert dvr2.track("aa:bb:cc:dd:ee:01")[0].kind == "pmkid"


def test_load_failsafe_on_missing_file(tmp_path):
    assert AirwaveDVR(path=str(tmp_path / "nope.jsonl")).load() == 0
