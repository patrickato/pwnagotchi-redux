"""Hardware-free tests for BLE tracker / skimmer detector."""
from __future__ import annotations

from redux.detect import AlertKind, BLETrackerDetector, Frame, FrameType


def ble(
    ts: float,
    *,
    addr: str = "aa:bb:cc:dd:ee:01",
    name: str = "",
    company_id: str = "",
    service_uuid: str = "",
) -> Frame:
    return Frame(
        type=FrameType.BLE_ADV,
        ts=ts,
        ble_addr=addr,
        ble_name=name,
        ble_company_id=company_id,
        ble_service_uuid=service_uuid,
    )


def test_apple_company_id_flags_tracker():
    d = BLETrackerDetector()
    a = d.feed(ble(1.0, company_id="0x004C", name=""))
    assert a is not None
    assert a.kind is AlertKind.BLE_TRACKER
    assert "Apple" in a.reason or "0x004c" in a.reason.lower()
    assert a.severity == "warning"


def test_apple_cid_bare_hex_normalized():
    d = BLETrackerDetector()
    a = d.feed(ble(1.0, company_id="4c"))
    assert a is not None
    assert a.kind is AlertKind.BLE_TRACKER


def test_hc05_name_flags_skimmer():
    d = BLETrackerDetector()
    a = d.feed(ble(1.0, addr="11:22:33:44:55:66", name="HC-05-ABC"))
    assert a is not None
    assert a.kind is AlertKind.BLE_SKIMMER
    assert a.severity == "critical"
    assert "HC-05" in a.reason or "skimmer" in a.reason.lower()


def test_benign_ble_quiet():
    d = BLETrackerDetector()
    a = d.feed(ble(1.0, company_id="0x00e0", name="Pixel Buds"))
    assert a is None


def test_non_ble_frames_ignored():
    d = BLETrackerDetector()
    f = Frame(type=FrameType.BEACON, ts=1.0, bssid="11:22:33:44:55:66", ssid="X")
    assert d.feed(f) is None


def test_dedupe_same_addr():
    d = BLETrackerDetector()
    f = ble(1.0, company_id="0x004c")
    assert d.feed(f) is not None
    assert d.feed(f) is None
