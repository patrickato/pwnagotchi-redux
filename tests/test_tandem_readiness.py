"""Two-radio execution preflight: pure synthetic evidence, never a live RF test."""
from dataclasses import replace

import pytest

from redux.engine.tandem_readiness import plan_measured_tandem
from redux.radio.dual_radio import RadioEvidence
from redux.radio.orchestrator import Radio
from redux.radio.split_integration import MeasuredRadio


def measured():
    onboard = MeasuredRadio(
        Radio("wlan0", phy="phy0", monitor=True, inject=True,
              bands=frozenset({"2.4"}), driver="brcmfmac", onboard=True),
        RadioEvidence("phy0", "monitor", 803, 12, True),
        3, "onboard", True, "capture_exclusive")
    external = MeasuredRadio(
        Radio("wlan1", phy="phy1", monitor=True, inject=True,
              bands=frozenset({"2.4", "5"}), driver="mt76x2u", usb_gen=3),
        RadioEvidence("phy1", "monitor", 803, 7, True),
        8, "usb", True, "capture_exclusive")
    return onboard, external


def plan(records=None, **kw):
    defaults = dict(ethernet_management_verified=True,
                    bettercap_present=True, angryoxide_present=True)
    defaults.update(kw)
    return plan_measured_tandem(measured() if records is None else records, **defaults)


def test_measured_split_matches_user_dual_engine_roles_but_never_activates():
    ready = plan()
    assert ready.state == "radio_qualified"
    assert ready.activation_authorized is False
    assert ready.usb_lease.iface == "wlan1"
    assert ready.usb_lease.phy == "phy1" and ready.usb_lease.ifindex == 8
    mapping = {b.iface: b for b in ready.bindings}
    assert mapping["wlan0"].engine == "bettercap"
    assert mapping["wlan1"].engine == "angryoxide"
    assert mapping["wlan0"].role == "scan"
    assert mapping["wlan1"].role == "capture"
    assert {b.band_plan for b in ready.bindings} == {"2.4", "5"}
    assert all(b.passive for b in ready.bindings)  # even with inject=True
    public = ready.to_dict()
    assert public["activation_authorized"] is False
    assert all("phy" not in row and "ifindex" not in row for row in public["bindings"])


@pytest.mark.parametrize("change", [
    "usb_frames_unknown", "usb_zero_frames", "usb_eth_link", "usb_nm_owner",
    "onboard_frames_unknown", "onboard_zero_frames", "onboard_eth_link",
    "onboard_nm_owner", "stale_usb", "stale_onboard", "duplicate_phy",
    "duplicate_ifindex", "wrong_usb_transport", "missing_radio",
])
def test_uncertain_or_conflicting_evidence_never_qualifies(change):
    onboard, external = measured()
    if change == "usb_frames_unknown":
        external = replace(external, evidence=replace(external.evidence, decoded_frames=None))
    elif change == "usb_zero_frames":
        external = replace(external, evidence=replace(external.evidence, decoded_frames=0))
    elif change == "usb_eth_link":
        external = replace(external, evidence=replace(external.evidence, arphrd=1))
    elif change == "usb_nm_owner":
        external = replace(external, owner="networkmanager")
    elif change == "onboard_frames_unknown":
        onboard = replace(onboard, evidence=replace(onboard.evidence, decoded_frames=None))
    elif change == "onboard_zero_frames":
        onboard = replace(onboard, evidence=replace(onboard.evidence, decoded_frames=0))
    elif change == "onboard_eth_link":
        onboard = replace(onboard, evidence=replace(onboard.evidence, arphrd=1))
    elif change == "onboard_nm_owner":
        onboard = replace(onboard, owner="networkmanager")
    elif change == "stale_usb":
        external = replace(external, fresh=False)
    elif change == "stale_onboard":
        onboard = replace(onboard, fresh=False)
    elif change == "duplicate_phy":
        external = replace(external, capabilities=replace(external.capabilities, phy="phy0"),
                           evidence=replace(external.evidence, phy="phy0"))
    elif change == "duplicate_ifindex":
        external = replace(external, ifindex=onboard.ifindex)
    elif change == "wrong_usb_transport":
        external = replace(external, transport="onboard")
    elif change == "missing_radio":
        external = None
    rows = (onboard,) if external is None else (onboard, external)
    result = plan(rows)
    assert result.state in {"blocked", "candidate"}
    assert not result.bindings and result.usb_lease is None
    assert result.activation_authorized is False


@pytest.mark.parametrize("option", [
    {"ethernet_management_verified": False},
    {"ethernet_management_verified": "yes"},
    {"bettercap_present": False},
    {"bettercap_present": 1},
    {"angryoxide_present": False},
    {"angryoxide_present": "installed"},
])
def test_mandatory_independent_engine_and_management_proofs(option):
    result = plan(**option)
    assert result.state == "blocked"
    assert not result.bindings


def test_no_fallback_to_alternate_radio_selection_when_planner_disagrees():
    onboard, external = measured()
    # The untrusted high-band onboard claim would make generic RECON choose
    # the wrong primary; this bridge never repoints the physical engines.
    onboard = replace(onboard, capabilities=replace(
        onboard.capabilities, bands=frozenset({"2.4", "5", "6"})))
    external = replace(external, capabilities=replace(
        external.capabilities, bands=frozenset({"2.4"})))
    result = plan((onboard, external))
    assert result.state == "blocked"
    assert "planner" in result.reasons[0]


def test_boolean_frame_count_does_not_count_as_received_packets():
    onboard, external = measured()
    external = replace(external, evidence=replace(external.evidence, decoded_frames=True))
    result = plan((onboard, external))
    assert result.state != "radio_qualified"
    assert not result.bindings
