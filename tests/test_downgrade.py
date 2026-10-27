"""WPA3 transition / downgrade classifier."""
from redux.classify.downgrade import assess_downgrade


def test_wpa3_transition_is_downgradable():
    f = assess_downgrade({"akm": ["SAE", "PSK"]})
    assert f.mode == "wpa3-transition" and f.downgradable
    assert "WPA2-PSK path" in f.reason


def test_wpa3_only_is_not_downgradable():
    f = assess_downgrade({"akm": ["SAE"]})
    assert f.mode == "wpa3-only" and not f.downgradable


def test_wpa2_psk_is_directly_crackable():
    f = assess_downgrade({"akm": ["PSK"]})
    assert f.mode == "wpa2" and f.downgradable


def test_enterprise_is_not_a_psk_downgrade():
    f = assess_downgrade({"akm": ["EAP"]})
    assert f.mode == "enterprise" and not f.downgradable


def test_open_network():
    f = assess_downgrade({"encryption": "open"})
    assert f.mode == "open" and not f.downgradable


def test_tolerates_string_encryption_shape():
    # a transition AP reported as a single string blob
    f = assess_downgrade({"encryption": "wpa2-psk", "authentication": "sae psk"})
    assert f.mode == "wpa3-transition" and f.downgradable
