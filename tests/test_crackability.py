from redux.classify import classify, triage, SecType, PMF


def test_wpa2_psk_is_crackable():
    a = classify({"encryption": "WPA2", "authentication": "PSK", "cipher": "CCMP"})
    assert a.sec_type is SecType.WPA2_PSK
    assert a.offline_crackable and a.capture_worth_it
    assert "22000" in a.method


def test_wpa3_sae_is_not_crackable_and_pmf_required():
    a = classify({"encryption": "WPA3", "authentication": "SAE"})
    assert a.sec_type is SecType.WPA3_SAE
    assert not a.offline_crackable
    assert not a.capture_worth_it
    assert a.pmf is PMF.REQUIRED
    assert "no offline path" in a.reason.lower()


def test_wpa3_transition_is_crackable_via_wpa2_path():
    # the nuance: SAE + PSK advertised together = transition mode
    a = classify({"encryption": "WPA2/WPA3", "authentication": ["PSK", "SAE"]})
    assert a.sec_type is SecType.WPA3_TRANSITION
    assert a.offline_crackable and a.capture_worth_it
    assert a.pmf is PMF.OPTIONAL
    assert "wpa2 path" in a.method.lower()


def test_enterprise_is_not_a_psk_target():
    a = classify({"encryption": "WPA2", "authentication": "802.1X"})
    assert a.sec_type is SecType.ENTERPRISE
    assert not a.offline_crackable


def test_owe_and_open_have_nothing_to_crack():
    owe = classify({"encryption": "OWE"})
    assert owe.sec_type is SecType.OWE and not owe.offline_crackable
    opn = classify({"encryption": ""})
    assert opn.sec_type is SecType.OPEN and not opn.offline_crackable
    assert opn.pmf is PMF.NONE


def test_wep_flagged_separately_not_wpa_pipeline():
    a = classify({"encryption": "WEP"})
    assert a.sec_type is SecType.WEP
    assert a.offline_crackable
    assert "IV" in a.method  # a different (IV) attack, not the WPA 22000 path
    assert any("legacy" in n.lower() for n in a.notes)


def test_explicit_pmf_field_overrides_inference():
    a = classify({"encryption": "WPA2", "authentication": "PSK", "pmf": "required"})
    assert a.pmf is PMF.REQUIRED  # stated, even though WPA2 would infer NONE


def test_wps_surfaced_as_note():
    a = classify({"encryption": "WPA2", "authentication": "PSK", "wps": True})
    assert any("wps" in n.lower() for n in a.notes)


def test_triage_ranks_crackable_first():
    nets = [
        {"essid": "sae", "encryption": "WPA3", "authentication": "SAE"},
        {"essid": "psk", "encryption": "WPA2", "authentication": "PSK"},
        {"essid": "open", "encryption": ""},
        {"essid": "trans", "encryption": "WPA2/WPA3", "authentication": ["PSK", "SAE"]},
    ]
    ranked = triage(nets)
    order = [n["essid"] for n, _ in ranked]
    # WPA2-PSK then transition first; SAE and open sink to the bottom
    assert order[0] == "psk"
    assert order[1] == "trans"
    assert order.index("sae") > order.index("trans")
    assert order[-1] == "open"
