"""USB capture / onboard connectivity plan never issues device commands."""
import pytest

from redux.radio.orchestrator import Radio
from redux.radio.dual_radio import RadioEvidence, plan_dual_radio


ONBOARD = Radio("wlan0", phy="phy0", onboard=True, monitor=True,
                driver="brcmfmac", bands=frozenset({"2.4", "5"}))
ALFA = Radio("wlan1", phy="phy1", onboard=False, monitor=True,
             driver="mt76x2u", usb_gen=3, bands=frozenset({"2.4", "5"}))
SECOND_USB = Radio("wlan2", phy="phy2", onboard=False, monitor=True,
                   usb_gen=2, bands=frozenset({"2.4", "5", "6"}))
MGMT = RadioEvidence("phy0", mode="managed", owner_verified=True,
                     management_connected=True)
RX = RadioEvidence("phy1", mode="monitor", arphrd=803,
                   decoded_frames=16, owner_verified=True)


def plan(radios=(ONBOARD, ALFA), evidence=None):
    return plan_dual_radio(radios, evidence if evidence is not None else {
        "wlan0": MGMT, "wlan1": RX
    })


def test_verified_ethernet_header_does_not_masquerade_as_wifi():
    candidate = plan(evidence={"wlan0": MGMT, "wlan1": RadioEvidence(
        "phy1", "monitor", arphrd=1, decoded_frames=44,
        owner_verified=True)})
    assert candidate.state == "candidate"
    assert candidate.capture_iface == "wlan1"
    assert candidate.activation_authorized is False


def test_verified_split_requires_actual_observed_native_reception():
    result = plan()
    assert result.state == "receive_verified"
    assert result.management_iface == "wlan0"
    assert result.capture_iface == "wlan1"
    assert result.activation_authorized is False


@pytest.mark.parametrize("evidence", [
    RadioEvidence("phy1", "monitor", arphrd=803, decoded_frames=0,
                  owner_verified=True),
    RadioEvidence("phy1", "monitor", arphrd=803, decoded_frames=None,
                  owner_verified=True),
    RadioEvidence("phy1", "managed", arphrd=803, decoded_frames=10,
                  owner_verified=True),
    RadioEvidence("phy1", "monitor", arphrd=803, decoded_frames=10,
                  owner_verified=False),
    RadioEvidence("phy1", "monitor", arphrd=True, decoded_frames=10,
                  owner_verified=True),
    RadioEvidence("phy1", "monitor", arphrd=803, decoded_frames=True,
                  owner_verified=True),
    RadioEvidence("phy0", "monitor", arphrd=803, decoded_frames=16,
                  owner_verified=True),
])
def test_candidate_does_not_claim_completed_capture(evidence):
    result = plan(evidence={"wlan0": MGMT, "wlan1": evidence})
    assert result.state == "candidate"
    assert result.activation_authorized is False


@pytest.mark.parametrize("evidence", [
    RadioEvidence("phy0", "monitor", owner_verified=True,
                  management_connected=True),
    RadioEvidence("phy0", "managed", owner_verified=False,
                  management_connected=True),
    RadioEvidence("phy0", "managed", owner_verified=True,
                  management_connected=False),
    RadioEvidence("phy99", "managed", owner_verified=True,
                  management_connected=True),
])
def test_management_radio_cannot_be_taken_for_capture(evidence):
    result = plan(evidence={"wlan0": evidence, "wlan1": RX})
    assert result.state == "blocked"
    assert result.capture_iface is None


def test_one_radio_safely_blocks_without_fallback_to_wlan0():
    r = plan(radios=(ONBOARD,), evidence={"wlan0": MGMT})
    assert r.state == "blocked"
    assert r.capture_iface is None


def test_phy_identity_conflicts_block_the_split_even_if_ifaces_differ():
    same = Radio("wlan1", phy="phy0", monitor=True, usb_gen=3)
    r = plan(radios=(ONBOARD, same))
    assert r.state == "blocked"
    assert "PHY" in r.reasons[0]


def test_duplicate_ifaces_and_invalid_inventory_block():
    assert plan(radios=(ONBOARD, ONBOARD)).state == "blocked"
    invalid = Radio("wlan1", phy="", monitor=True)
    assert plan(radios=(ONBOARD, invalid)).state == "blocked"


def test_usb_removal_is_not_silent_onboard_capture_promotion():
    assert plan(radios=(ONBOARD,), evidence={"wlan0": MGMT}).capture_iface is None


def test_no_monitor_capable_usb_adapter_is_not_selected():
    bare = Radio("wlan1", phy="phy1", monitor=False, usb_gen=3)
    assert plan(radios=(ONBOARD, bare)).state == "blocked"


def test_multiple_adapters_prefer_verified_reception_not_heurstic_bandwidth():
    observations = {"wlan0": MGMT, "wlan1": RX, "wlan2":
                    RadioEvidence("phy2", "monitor", arphrd=803,
                                  decoded_frames=0, owner_verified=True)}
    r = plan(radios=(ONBOARD, SECOND_USB, ALFA), evidence=observations)
    assert r.capture_iface == "wlan1" and r.state == "receive_verified"


def test_split_data_is_pure_and_does_not_touch_input_state():
    radios = [ONBOARD, ALFA]
    evidence = {"wlan0": MGMT, "wlan1": RX}
    assert plan(radios=radios, evidence=evidence).state == "receive_verified"
    assert radios == [ONBOARD, ALFA]
    assert evidence == {"wlan0": MGMT, "wlan1": RX}
