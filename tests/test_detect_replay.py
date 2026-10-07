"""Hardware-free tests for frame replay harness (Grok D7)."""
import json

from redux.detect.engine import DetectEngine
from redux.detect.frames import FrameType
from redux.detect.replay import frame_from_dict, load_frames_json, replay_json


def test_frame_from_dict():
    f = frame_from_dict({"type": "deauth", "ts": 1.0, "bssid": "AA:BB:CC:DD:EE:01"})
    assert f.type is FrameType.DEAUTH
    assert f.bssid == "aa:bb:cc:dd:ee:01"


def test_load_and_replay_deauth_burst():
    payload = [
        {"type": "deauth", "ts": 0.1 * i, "bssid": "aa:bb:cc:dd:ee:01"}
        for i in range(25)
    ]
    frames = load_frames_json(payload)
    assert len(frames) == 25
    from redux.detect.deauth_flood import DeauthFloodDetector

    eng = DetectEngine(deauth=DeauthFloodDetector(window_s=5.0, threshold=20))
    alerts = eng.feed_many(frames)
    assert any(a.kind.value == "deauth_flood" for a in alerts)


def test_replay_json_string():
    s = json.dumps({"frames": [{"type": "beacon", "ts": 1.0, "ssid": "X", "bssid": "11:11:11:11:11:11"}]})
    alerts = replay_json(s)
    assert isinstance(alerts, list)


def test_frame_from_dict_preserves_pmf():
    # regression: pmf was dropped on load, making PMFMissingDetector false-positive.
    f = frame_from_dict({"type": "beacon", "ssid": "Home", "security": "wpa2-psk", "pmf": "required"})
    assert f.pmf == "required"
