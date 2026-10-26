"""Device identification — what is it, and what can I do with/to it."""
from redux.intel.device_id import (
    DeviceSignals, identify, actions_for,
)


def test_hostname_names_the_product():
    g = identify(DeviceSignals(hostname="Roomba-3F2A"))
    assert g.category == "vacuum" and "Roomba" in g.product and g.confidence == "high"
    assert any("Roomba" in r for r in g.reasons)


def test_mdns_identifies_a_chromecast():
    g = identify(DeviceSignals(mdns_services=("_googlecast._tcp",)))
    assert g.category == "streamer" and "Cast" in g.product


def test_ble_name_and_company_identify_an_echo():
    g = identify(DeviceSignals(ble_name="echo-dot-abc", ble_company_id="0171"))
    assert g.category == "voice-assistant" and "Echo" in g.product


def test_vendor_only_is_a_low_confidence_hint():
    g = identify(DeviceSignals(vendor="iRobot Corporation"))
    assert g.category == "vacuum" and g.confidence == "low"
    assert any("OUI vendor" in r for r in g.reasons)


def test_agreement_lifts_confidence():
    # vendor says vacuum (low) + hostname says Roomba (high) → high, multiple agree
    g = identify(DeviceSignals(vendor="iRobot", hostname="roomba-123"))
    assert g.category == "vacuum" and g.confidence == "high"


def test_unknown_when_no_signal():
    g = identify(DeviceSignals(mac="de:ad:be:ef:00:01"))
    assert g.category in ("unknown",) or g.confidence == "low"


def test_guess_carries_what_you_can_do_to_it():
    g = identify(DeviceSignals(hostname="HP-LaserJet-Printer"))
    assert g.category == "printer"
    dos = [a["do"] for a in g.actions]
    assert any("IPP" in d or "9100" in d for d in dos)          # printer-specific surface
    assert any("services" in d for d in dos)                    # generic on-LAN too
    assert all("via" in a for a in g.actions)


def test_actions_for_camera_has_stream_and_default_creds():
    acts = actions_for("camera")
    dos = " ".join(a["do"] for a in acts).lower()
    assert "rtsp" in dos or "stream" in dos
    assert "default" in dos and "services" in dos               # specific + on-LAN


def test_wearable_is_ble_not_on_lan():
    acts = actions_for("wearable")
    dos = " ".join(a["do"] for a in acts).lower()
    assert "gatt" in dos or "ble" in dos
    assert "ports/services" not in dos                          # not a LAN host
