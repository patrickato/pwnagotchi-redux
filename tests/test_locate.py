"""Direction finding — RSSI source localization (engine + ObservationLog rssi + CLI)."""
from types import SimpleNamespace

from redux.intel.locate import locate, Reading, LocateResult


def _r(ts, rssi, lat, lon):
    return SimpleNamespace(ts=ts, rssi=rssi, lat=lat, lon=lon)


def test_estimate_pulled_toward_the_strong_position():
    # weak readings far west, a strong reading at the east point -> estimate skews east
    rs = [_r(1, -80, 42.50, -83.20), _r(2, -78, 42.50, -83.15),
          _r(3, -40, 42.50, -83.00)]            # loud here
    res = locate(rs)
    assert res.strongest_rssi == -40 and res.strongest_at == (42.50, -83.00)
    assert res.est_lon > -83.15                  # weighted toward the loud (eastern) spot


def test_warmer_when_recent_readings_are_stronger():
    rs = [_r(1, -80, 42.5, -83.2), _r(2, -78, 42.5, -83.19),
          _r(3, -50, 42.5, -83.1), _r(4, -45, 42.5, -83.05)]
    assert locate(rs, window=2).trend == "warmer"


def test_colder_when_recent_readings_are_weaker():
    rs = [_r(1, -45, 42.5, -83.1), _r(2, -50, 42.5, -83.12),
          _r(3, -78, 42.5, -83.2), _r(4, -82, 42.5, -83.25)]
    assert locate(rs, window=2).trend == "colder"


def test_readings_from_one_spot_are_honest_about_low_confidence():
    rs = [_r(i, -50 - i, 42.5, -83.1) for i in range(5)]   # same place
    res = locate(rs)
    assert "move around" in res.confidence or "one spot" in res.confidence


def test_bearing_and_guidance_present_with_movement():
    rs = [_r(1, -80, 42.50, -83.20), _r(2, -60, 42.50, -83.10), _r(3, -40, 42.50, -83.00)]
    res = locate(rs)
    assert res.bearing in ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    assert "strongest" in res.guidance


def test_no_usable_readings():
    res = locate([_r(1, None, 42.5, -83.1), _r(2, -50, None, None)])  # missing rssi / gps
    assert res.readings == 0 and "no" in res.guidance.lower()


# --- ObservationLog rssi round-trip ----------------------------------------- #

def test_observation_log_persists_and_loads_rssi(tmp_path):
    from redux.geo.follower import ObservationLog
    log = ObservationLog(str(tmp_path / "obs.jsonl"))
    log.record("aa:bb:cc:dd:ee:ff", 100.0, lat=42.5, lon=-83.1, rssi=-55)
    log.record("aa:bb:cc:dd:ee:ff", 200.0, lat=42.5, lon=-83.1)     # no rssi -> None, still ok
    rows = ObservationLog(str(tmp_path / "obs.jsonl")).load()
    assert rows[0].rssi == -55 and rows[1].rssi is None


# --- CLI -------------------------------------------------------------------- #

def test_cmd_locate_runs_on_logged_readings(tmp_path, capsys):
    from redux.cli import cmd_locate
    from redux.geo.follower import ObservationLog
    import json as _json
    log = ObservationLog(str(tmp_path / "obs.jsonl"))
    mac = "aa:bb:cc:dd:ee:ff"
    for i, (rssi, lon) in enumerate([(-80, -83.20), (-60, -83.10), (-40, -83.00)]):
        log.record(mac, float(i), lat=42.50, lon=lon, rssi=rssi)
    base = dict(target=mac, obs=str(tmp_path / "obs.jsonl"), window=3)
    assert cmd_locate(SimpleNamespace(json=False, **base)) == 0
    out = capsys.readouterr().out
    assert "LOCATING" in out and "strongest" in out
    assert cmd_locate(SimpleNamespace(json=True, **base)) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["readings"] == 3 and payload["strongest_rssi"] == -40


def test_cmd_locate_no_readings_is_honest(tmp_path, capsys):
    from redux.cli import cmd_locate
    assert cmd_locate(SimpleNamespace(target="aa:bb", obs=str(tmp_path / "none.jsonl"),
                                      window=3, json=False)) == 0
    assert "no readings" in capsys.readouterr().out.lower()
