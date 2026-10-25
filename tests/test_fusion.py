"""Co-occurrence fusion — devices that travel together (engine + CLI)."""
from types import SimpleNamespace

from redux.intel.fusion import fuse, FusionGroup


def _o(mac, ts, lat=42.5, lon=-83.1):
    return SimpleNamespace(mac=mac, ts=ts, lat=lat, lon=lon)


def test_two_devices_always_together_are_grouped():
    # A and B seen in the same place across 4 time windows (120s buckets)
    obs = []
    for w in range(4):
        obs += [_o("aa:aa", w * 120), _o("bb:bb", w * 120)]
    groups = fuse(obs, min_co=2)
    assert len(groups) == 1
    assert set(groups[0].macs) == {"aa:aa", "bb:bb"} and groups[0].co_windows == 4


def test_devices_that_only_cross_once_are_not_linked():
    # A present in 4 windows, C shares only 1 of them -> below min_co
    obs = [_o("aa:aa", w * 120) for w in range(4)] + [_o("cc:cc", 0)]
    assert fuse(obs, min_co=2) == []


def test_different_place_same_time_does_not_co_occur():
    obs = [_o("aa:aa", 0, lat=42.5, lon=-83.1), _o("bb:bb", 0, lat=10.0, lon=10.0),
           _o("aa:aa", 120, lat=42.5, lon=-83.1), _o("bb:bb", 120, lat=10.0, lon=10.0)]
    assert fuse(obs, min_co=2) == []        # same time slots, different cells


def test_confidence_grades_with_evidence():
    # 6 shared windows, identical sets -> strong
    obs = []
    for w in range(6):
        obs += [_o("aa:aa", w * 120), _o("bb:bb", w * 120)]
    g = fuse(obs, min_co=2)[0]
    assert g.confidence == "strong" and g.score == 1.0


def test_three_device_group_via_transitive_cotravel():
    obs = []
    for w in range(3):
        obs += [_o("aa:aa", w * 120), _o("bb:bb", w * 120), _o("cc:cc", w * 120)]
    g = fuse(obs, min_co=2)[0]
    assert set(g.macs) == {"aa:aa", "bb:bb", "cc:cc"}


def test_dicts_accepted_and_empty_is_empty():
    assert fuse([]) == []
    obs = [{"mac": "aa:aa", "ts": 0, "lat": 1.0, "lon": 1.0},
           {"mac": "bb:bb", "ts": 0, "lat": 1.0, "lon": 1.0},
           {"mac": "aa:aa", "ts": 120, "lat": 1.0, "lon": 1.0},
           {"mac": "bb:bb", "ts": 120, "lat": 1.0, "lon": 1.0}]
    assert len(fuse(obs, min_co=2)) == 1


# --- CLI -------------------------------------------------------------------- #

def test_cmd_fuse_reads_log_and_reports(tmp_path, capsys):
    from redux.cli import cmd_fuse
    from redux.geo.follower import ObservationLog
    import json as _json
    log = ObservationLog(str(tmp_path / "obs.jsonl"))
    for w in range(4):
        log.record("aa:aa:aa:aa:aa:aa", w * 120.0, lat=42.5, lon=-83.1)
        log.record("bb:bb:bb:bb:bb:bb", w * 120.0, lat=42.5, lon=-83.1)
    base = dict(log=str(tmp_path / "obs.jsonl"), window_seconds=120.0, precision=7,
                min_co=2, min_score=0.3)
    assert cmd_fuse(SimpleNamespace(json=False, **base)) == 0
    out = capsys.readouterr().out
    assert "travel together" in out and "aa:aa:aa:aa:aa:aa" in out
    assert cmd_fuse(SimpleNamespace(json=True, **base)) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert len(payload) == 1 and payload[0]["co_windows"] == 4


def test_cmd_fuse_empty_log_is_honest(tmp_path, capsys):
    from redux.cli import cmd_fuse
    assert cmd_fuse(SimpleNamespace(log=str(tmp_path / "none.jsonl"), window_seconds=120.0,
                                    precision=7, min_co=2, min_score=0.3, json=False)) == 0
    assert "no observation history" in capsys.readouterr().out
