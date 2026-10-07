"""Hardware-free tests for BLE flood detector (Grok D4)."""
from redux.detect.alerts import AlertKind
from redux.detect.ble_flood import BLEFloodDetector
from redux.detect.frames import Frame, FrameType


def test_flood_fires():
    d = BLEFloodDetector(window_s=5.0, unique_addr_threshold=5)
    frames = [
        Frame(type=FrameType.BLE_ADV, ts=0.1 * i, ble_addr=f"aa:bb:cc:dd:ee:{i:02x}")
        for i in range(5)
    ]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    assert alerts[0].kind is AlertKind.BLE_FLOOD


def test_repeat_addr_quiet():
    d = BLEFloodDetector(window_s=5.0, unique_addr_threshold=5)
    frames = [
        Frame(type=FrameType.BLE_ADV, ts=0.1 * i, ble_addr="aa:aa:aa:aa:aa:01")
        for i in range(20)
    ]
    assert d.feed_many(frames) == []


def test_ble_flood_rearms_after_flood_subsides():
    # regression: _fired used to latch forever, so the detector went dead after
    # the first flood. It must re-arm once the window drops below threshold.
    det = BLEFloodDetector(window_s=5.0, unique_addr_threshold=3)
    def adv(ts, addr):
        return det.feed(Frame(type=FrameType.BLE_ADV, ts=ts, ble_addr=addr))
    adv(0.0, "a"); adv(0.1, "b")
    assert adv(0.2, "c") is not None            # first flood fires
    assert adv(100.0, "d") is None              # window cleared -> re-arms
    adv(100.1, "e")
    assert adv(100.2, "f") is not None          # a later flood fires again
