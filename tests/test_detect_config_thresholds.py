"""Hardware-free tests for per-detector config thresholds (Grok DD7)."""
from redux.detect.config import DEFAULTS, describe_defaults, merge_options
from redux.detect.deauth_flood import DeauthFloodDetector
from redux.detect.engine import DetectEngine
from redux.detect.pnl import PNLHarvester
from redux.detect.registry import build_from_registry


def test_describe_defaults_covers_all_keys():
    rows = describe_defaults()
    keys = {r["key"] for r in rows}
    assert keys == set(DEFAULTS.keys())
    assert all("owner" in r and "value" in r for r in rows)


def test_merge_options_override():
    m = merge_options({"deauth_threshold": 99})
    assert m["deauth_threshold"] == 99
    assert m["deauth_window_s"] == DEFAULTS["deauth_window_s"]


def test_registry_applies_deauth_override():
    dets = build_from_registry(["deauth_flood"], options={"deauth_threshold": 7})
    assert len(dets) == 1
    assert isinstance(dets[0], DeauthFloodDetector)
    assert dets[0].threshold == 7


def test_registry_applies_pnl_override():
    dets = build_from_registry(["pnl"], options={"pnl_loud_threshold": 3})
    assert isinstance(dets[0], PNLHarvester)
    assert dets[0].loud_threshold == 3


def test_engine_options_reach_detector():
    eng = DetectEngine(names=["deauth_flood"], options={"deauth_threshold": 11})
    assert eng.detector_count == 1
    d = eng._detectors[0]
    assert isinstance(d, DeauthFloodDetector)
    assert d.threshold == 11
