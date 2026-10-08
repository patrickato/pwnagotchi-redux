"""Hardware-free tests over recorded hostile-capture fixtures (Grok DD6)."""
from __future__ import annotations

from pathlib import Path

from redux.detect.beacon_spam import BeaconSpamDetector
from redux.detect.deauth_flood import DeauthFloodDetector
from redux.detect.engine import DetectEngine
from redux.detect.replay import load_frames_json, replay

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "detect"


def test_deauth_flood_fixture_fires():
    path = FIXTURES / "deauth_flood.json"
    frames = load_frames_json(path)
    assert len(frames) == 25
    eng = DetectEngine(
        deauth=DeauthFloodDetector(window_s=5.0, threshold=20),
        names=[],  # unused when legacy kwargs set
    )
    # Prefer explicit single-detector path for a tight assertion
    eng = DetectEngine(detectors=[DeauthFloodDetector(window_s=5.0, threshold=20)])
    alerts = replay(frames, engine=eng)
    assert any(a.kind.value == "deauth_flood" for a in alerts)
    assert all(a.reason for a in alerts)


def test_beacon_spam_fixture_fires():
    frames = load_frames_json(FIXTURES / "beacon_spam.json")
    eng = DetectEngine(
        detectors=[
            BeaconSpamDetector(
                window_s=10.0,
                unique_ssid_threshold=30,
                unique_bssid_threshold=30,
            )
        ]
    )
    alerts = replay(frames, engine=eng)
    assert any(a.kind.value == "beacon_spam" for a in alerts)


def test_benign_home_fixture_quiet_on_core_detectors():
    frames = load_frames_json(FIXTURES / "benign_home.json")
    eng = DetectEngine(
        detectors=[
            DeauthFloodDetector(window_s=5.0, threshold=20),
            BeaconSpamDetector(
                window_s=10.0,
                unique_ssid_threshold=30,
                unique_bssid_threshold=40,
            ),
        ]
    )
    alerts = replay(frames, engine=eng)
    assert alerts == []


def test_fixtures_dir_present():
    assert (FIXTURES / "deauth_flood.json").is_file()
    assert (FIXTURES / "beacon_spam.json").is_file()
    assert (FIXTURES / "benign_home.json").is_file()
