"""Bridge tests: bettercap driver Events -> detector Frames (the integration seam).

Covers honest coverage (what maps, what is deliberately left unmapped) and a full
driver-normalize -> bridge -> DetectEngine path for the BLE detectors.
"""
from redux.core.event_bridge import event_to_frame, events_to_frames
from redux.detect.frames import FrameType
from redux.detect import DetectEngine
from redux.engine import Event, normalize_event


def _ev(type_, data, at=1000.0):
    return Event(type=type_, at=at, data=data, reason="", raw_tag="")


def test_ap_new_maps_to_beacon():
    f = event_to_frame(_ev("ap.new", {"mac": "aa:bb:cc:dd:ee:ff", "essid": "HomeNet", "encryption": "wpa2"}))
    assert f is not None and f.type is FrameType.BEACON
    assert f.bssid == "aa:bb:cc:dd:ee:ff" and f.ssid == "HomeNet"


def test_ble_new_maps_to_ble_adv_with_present_fields_only():
    f = event_to_frame(_ev("ble.new", {
        "mac": "11:22:33:44:55:66", "name": "HC-05",
        "service_uuid": "feed", "rssi": -60,
    }))
    assert f is not None and f.type is FrameType.BLE_ADV
    assert f.ble_addr == "11:22:33:44:55:66" and f.src == "11:22:33:44:55:66"
    assert f.ble_name == "HC-05" and f.ble_service_uuid == "feed"
    # company_id was not provided and must NOT be invented
    assert f.ble_company_id == ""


def test_ble_new_without_address_is_dropped():
    # an advert with no address is not a usable sighting -> None, never faked
    assert event_to_frame(_ev("ble.new", {"name": "ghost"})) is None


def test_high_level_and_absent_events_are_unmapped():
    # handshake is a high-level capture signal, not an EAPOL frame sequence
    assert event_to_frame(_ev("handshake", {"ap": "aa:bb:cc:dd:ee:ff"})) is None
    assert event_to_frame(_ev("client.new", {"mac": "de:ad:be:ef:00:01"})) is None
    assert event_to_frame(_ev("ble.lost", {"mac": "11:22:33:44:55:66"})) is None


def test_ble_flood_fires_through_normalize_bridge_and_engine():
    # full chain: raw bettercap ble.device.new -> driver normalize -> bridge -> engine
    raw = [
        {"tag": "ble.device.new", "time": 2000.0, "data": {"mac": f"aa:bb:cc:00:00:{i:02x}"}}
        for i in range(21)  # > ble_flood default threshold (20) within the window
    ]
    events = [normalize_event(r) for r in raw]
    frames = events_to_frames(events)
    assert frames and all(f.type is FrameType.BLE_ADV for f in frames)
    eng = DetectEngine(names=["ble_flood"])
    alerts = []
    for f in frames:
        alerts.extend(eng.feed(f))
    assert any(a.kind.value == "ble_flood" for a in alerts)
