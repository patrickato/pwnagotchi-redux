"""Only synthetic saved inspection projections; no SSH, Pi or captured data."""
import json
import pytest
from redux.field_feedback import facts, encoded_facts


def fixture():
    return {"schema":1,"result":"UNKNOWN","physical_acceptance":"UNKNOWN",
      "evidence_origin":"saved_unsigned_session", "checks":[
       {"gate":"saved_observation_freshness","status":"UNKNOWN"}],
      "observations": {"display_metadata_state":"BLOCKED", "display_blockers":
        ["FRAMEBUFFER MISSING","ILI9486 MISSING","TOUCH DEVICE MISSING"],
      "radio_inventory_details":[
        {"radio":1,"transport":"onboard","arphrd":1,"kernel_mode":"monitor",
         "nm_managed":True,"nm_state":20,"conflicting_processes":1},
        {"radio":2,"transport":"usb","arphrd":803,"kernel_mode":"monitor",
         "nm_managed":True,"nm_state":20,"conflicting_processes":1}],
      "capture_processing": {"available":True,"fresh_at_collection":True,
          "artifact_count":5,"by_status":{"ready":1,"invalid":4},
          "last_scan":{"scanned":5,"outcomes":{"duplicate":5}}},
      "captures_available_mib":449,"live_runtime":{"live_state":"running"}}}


def test_realistic_historical_aggregate_is_honest():
    result = facts(fixture())
    assert result["physical_acceptance"] == "UNKNOWN"
    assert result["snapshot_freshness"] == "UNKNOWN"
    assert result["processing"]["by_status"] == {"invalid":4,"ready":1}
    assert result["processing"]["last_scan"]["outcomes"] == {"duplicate":5}
    assert result["storage_free_band"] == "64_to_511_mib"
    assert result["radios"][0]["link_type"] == 1
    assert result["radios"][1]["link_type"] == 803
    assert "usb_radiotap_not_reception_proof" in result["signals"]
    assert "ready_ledger_entry_needs_byte_audit" in result["signals"]


def test_secrets_and_untrusted_free_text_not_reflected():
    input_ = fixture()
    input_["secret"] = "SSH_KEY_PRIVATE_LAN"
    input_["observations"]["display_blockers"].append("PRIVATE_CONFIG_USER")
    input_["observations"]["radio_inventory_details"][0].update(
        iface="PRIVATE_INTERFACE", ssid="PRIVATE_SSID", path="/private/home/key")
    input_["observations"]["live_runtime"]["private_mac"]="PRIVATE_MAC"
    encoded = encoded_facts(input_)
    for val in ("PRIVATE", "SSH_KEY", "/private", "PRIVATE_SSID"):
        assert val not in encoded
    assert len(encoded.encode()) <= 8192


@pytest.mark.parametrize("override", [
    {"physical_acceptance":"PASS"}, {"result":"PASS"},
    {"evidence_origin":"strict_ssh_read_only_unsigned"}, {"schema":2},
    {"observations":None}, {"checks":None},
])
def test_rejects_untrusted_projection(override):
    bad = fixture();bad.update(override)
    with pytest.raises(ValueError): facts(bad)


def test_malformed_radio_ledgers_never_make_pass():
    data=fixture()
    data["observations"]["radio_inventory_details"]=[{"radio":-2,"transport":"PRIVATE","arphrd":-1,
          "kernel_mode":"PRIVATE","nm_managed":"true","conflicting_processes":99999}]
    data["observations"]["capture_processing"]["by_status"]={"ready":True,"invalid":4}
    output=facts(data)
    assert output["radios"][0]["transport"] is None
    assert output["radios"][0]["link_type"] is None
    assert output["processing"]["by_status"] is None
    assert output["signals"] == ["display_metadata_blocked"]


def test_unexpected_nonstring_blockers_do_not_echo():
    data=fixture();data["observations"]["display_blockers"]=["ILI9486 MISSING",123,{},"PRIVATE"]
    assert facts(data)["display"]["blockers"] == ["ILI9486 MISSING"]
