"""Unwanted-tracker follower analysis: persistence across time AND distance."""
from redux.geo import Sighting
from redux.geo.follower import follower_analysis, followers, analyze_mac


def _s(mac, ts, lat=None, lon=None):
    return Sighting(kind="ble", mac=mac, ts=ts, lat=lat, lon=lon, provenance="t")


def test_device_moving_with_you_is_flagged():
    # same tag seen over 20 min at 3 places ~500 m apart
    sl = [
        _s("tag", 0, 40.000, -80.000),
        _s("tag", 600, 40.003, -80.000),     # ~333 m north
        _s("tag", 1200, 40.006, -80.000),    # ~666 m north
    ]
    v = analyze_mac("tag", sl)
    assert v.following and v.score == 1.0
    assert "FOLLOWING" in v.reason and v.locations == 3


def test_stationary_device_is_not_a_follower():
    # seen many times over a long time but all at ONE spot (a neighbor's tag)
    sl = [_s("fixed", t, 40.0, -80.0) for t in (0, 600, 1200, 1800)]
    v = analyze_mac("fixed", sl)
    assert not v.following and "stationary" in v.reason


def test_brief_encounter_is_not_a_follower():
    # two distant points but only seconds apart (you passed it once)
    sl = [_s("passing", 0, 40.0, -80.0), _s("passing", 30, 40.01, -80.0)]
    v = analyze_mac("passing", sl)
    assert not v.following
    assert "min" in v.reason                       # flagged short duration


def test_no_location_cannot_be_judged():
    sl = [_s("nogps", t) for t in (0, 600, 1200, 1800)]
    v = analyze_mac("nogps", sl)
    assert not v.following and v.score == 0.0
    assert "no location data" in v.reason


def test_single_sighting_not_following():
    v = analyze_mac("once", [_s("once", 0, 40.0, -80.0)])
    assert not v.following


def test_gps_jitter_does_not_count_as_many_locations():
    # 4 fixes within a few meters of each other = one place, not four
    sl = [
        _s("jit", 0, 40.00000, -80.00000),
        _s("jit", 600, 40.00002, -80.00001),
        _s("jit", 1200, 40.00001, -80.00002),
        _s("jit", 1800, 40.00000, -80.00001),
    ]
    v = analyze_mac("jit", sl)
    assert v.locations == 1 and not v.following


def test_analysis_sorts_followers_first():
    follow = [
        _s("follow", 0, 40.0, -80.0),
        _s("follow", 600, 40.003, -80.0),
        _s("follow", 1200, 40.006, -80.0),
    ]
    fixed = [_s("fixed", t, 41.0, -81.0) for t in (0, 600, 1200)]
    verdicts = follower_analysis(follow + fixed)
    assert verdicts[0].mac == "follow" and verdicts[0].following
    only = followers(follow + fixed)
    assert [v.mac for v in only] == ["follow"]


# ---- ObservationLog (the time series the analysis needs) ---------------- #

def test_observation_log_records_and_loads(tmp_path):
    from redux.geo.follower import ObservationLog
    log = ObservationLog(tmp_path / "obs.jsonl", position=lambda: (40.0, -80.0))
    log.record("tag", 0)                 # position auto-tagged from provider
    log.record("tag", 600, lat=40.003, lon=-80.0)
    rows = log.load()
    assert len(rows) == 2
    assert rows[0].lat == 40.0 and rows[1].lat == 40.003


def test_observation_log_feeds_follower_analysis(tmp_path):
    from redux.geo.follower import ObservationLog, follower_analysis
    log = ObservationLog(tmp_path / "obs.jsonl")
    for t, lat in [(0, 40.000), (600, 40.003), (1200, 40.006)]:
        log.record("tag", t, lat=lat, lon=-80.0)
    verdicts = follower_analysis(log.load())
    assert verdicts[0].mac == "tag" and verdicts[0].following


def test_observation_log_observe_filters_ble(tmp_path):
    from redux.geo.follower import ObservationLog

    class _Ev:
        def __init__(self, type, data, at): self.type = type; self.data = data; self.at = at

    class _Em:
        def __init__(self, event): self.payload = {"event": event}

    log = ObservationLog(tmp_path / "obs.jsonl", position=lambda: (1.0, 2.0))
    log.observe(_Em(_Ev("ble.new", {"mac": "aa:bb:cc:dd:ee:ff"}, 10.0)))
    log.observe(_Em(_Ev("ap.new", {"mac": "11:22:33:44:55:66"}, 11.0)))   # not ble -> skipped
    rows = log.load()
    assert len(rows) == 1 and rows[0].mac == "aa:bb:cc:dd:ee:ff"


def test_observation_log_tolerates_corrupt_lines(tmp_path):
    from redux.geo.follower import ObservationLog
    p = tmp_path / "obs.jsonl"
    p.write_text('{"mac":"a","ts":1,"lat":1,"lon":2}\nGARBAGE\n\n')
    assert len(ObservationLog(p).load()) == 1
