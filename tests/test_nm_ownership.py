"""Offline ownership evidence: missing NM discovery never means unmanaged."""
import pytest

from redux.radio.nm_ownership import (assess_down_only_samples,
                                      assess_ownership, udev_ownership_policy)


@pytest.mark.parametrize(("properties", "expected"), [
    (None, "unknown"),
    ("", "not_declared"),
    ("DEVPATH=/devices/private\nID_NET_MANAGED_BY=org.freedesktop.NetworkManager\n",
     "names_networkmanager"),
    ("ID_NET_MANAGED_BY=io.systemd.Network\n", "names_other_manager"),
    ("ID_NET_MANAGED_BY=io.systemd.Network\nNM_UNMANAGED=true\n",
     "udev_requests_unmanaged_overridable"),
    ("NM_UNMANAGED=1\n", "udev_requests_unmanaged_overridable"),
    ("NM_UNMANAGED=no\nID_NET_MANAGED_BY=io.systemd.Network\n", "unknown"),
    ("ID_NET_MANAGED_BY=foreign\nID_NET_MANAGED_BY=other\n", "unknown"),
    ("NM_UNMANAGED=true\nNM_UNMANAGED=false\n", "unknown"),
    ("ID_NET_MANAGED_BY=io.systemd.Network\nNM_UNMANAGED=0\n",
     "udev_requests_managed"),
    ("SSID=my private network\nBSSID=00:11:22:33:44:55\n", "not_declared"),
])
def test_only_allowlisted_udev_fields_affect_policy(properties, expected):
    assert udev_ownership_policy(properties, supports_id_net_managed_by=True) == expected


def evaluate(**change):
    data = dict(
        nm_managed=None,
        nm_state=None,
        dbus_enumerated=False,
        udev_properties=None,
        kernel_interface_present=True,
        supports_id_net_managed_by=True,
    )
    data.update(change)
    return assess_ownership(**data)


def test_october_pi_report_classifies_missing_nm_as_unknown():
    evidence = evaluate()
    assert evidence.status == "not_enumerated_unknown"
    assert evidence.nm_enumerated is False
    assert evidence.permits_activation is False


def test_dbus_inventory_missing_is_not_proof_even_with_nmcli_absence():
    evidence = evaluate(dbus_enumerated=None)
    assert evidence.status == "unknown"
    assert evidence.permits_activation is False


def test_both_nm_paths_absent_with_explicit_non_nm_udev_policy():
    evidence = evaluate(udev_properties=(
        "ID_NET_MANAGED_BY=io.systemd.Network\nDEVPATH=/private/device\n"))
    assert evidence.status == "udev_policy_not_nm"
    assert evidence.permits_activation is False
    assert "private" not in repr(evidence)
    assert "io.systemd.Network" not in repr(evidence)


def test_udev_unmanaged_can_be_overridden_by_nm_runtime():
    evidence = evaluate(
        udev_properties="NM_UNMANAGED=1\nID_NET_MANAGED_BY=other\n")
    assert evidence.status == "udev_unmanaged_overridable"
    assert evidence.permits_activation is False


@pytest.mark.parametrize(("managed", "state", "dbus", "expected"), [
    (True, 20, True, "nm_managed"),
    (False, 10, True, "nm_explicitly_unmanaged"),
    (False, 10, None, "nm_explicitly_unmanaged"),
    (False, 30, True, "inconsistent"),
    (False, 10, False, "inconsistent"),
    (None, None, True, "nm_dbus_visible_unknown"),
])
def test_live_manager_claim_wins_over_udev_policy(managed, state, dbus, expected):
    evidence = evaluate(nm_managed=managed, nm_state=state,
                        dbus_enumerated=dbus,
                        udev_properties="ID_NET_MANAGED_BY=other\n")
    assert evidence.status == expected
    assert evidence.permits_activation is False


def test_no_kernel_device_never_accepts_ownership_proof():
    evidence = evaluate(
        nm_managed=False, nm_state=10, dbus_enumerated=True,
        kernel_interface_present=False)
    assert evidence.status == "no_kernel_interface"
    assert evidence.permits_activation is False


def test_udev_property_output_never_exposes_identifiers():
    evidence = evaluate(udev_properties=(
        "ID_NET_MANAGED_BY=org.freedesktop.NetworkManager\n"
        "DEVPATH=/sensitive/private/asset\n"
        "ID_NET_NAME_MAC=enx001122334455\n"
        "CONNECTION_ID=customer\n"))
    assert evidence.status == "not_enumerated_unknown"
    for sensitive in ("private", "customer", "001122334455"):
        assert sensitive not in repr(evidence)


@pytest.mark.parametrize(('support', 'policy'), [
    (False, 'managed_by_unsupported'), (None, 'managed_by_support_unverified'),
])
def test_managed_by_cannot_exclude_nm_without_verified_version_support(support, policy):
    properties = 'ID_NET_MANAGED_BY=other\n'
    assert udev_ownership_policy(properties, supports_id_net_managed_by=support) == policy
    evidence = evaluate(udev_properties=properties, supports_id_net_managed_by=support)
    assert evidence.udev_policy == policy
    assert evidence.status == 'not_enumerated_unknown'
    assert evidence.permits_activation is False


def test_managed_by_default_is_unverified_not_exclusion():
    assert udev_ownership_policy('ID_NET_MANAGED_BY=other\n') == 'managed_by_support_unverified'


@pytest.mark.parametrize('value', ['0', 'false', ''])
def test_explicit_false_unmanaged_suppresses_managed_by(value):
    evidence = evaluate(udev_properties=f'NM_UNMANAGED={value}\nID_NET_MANAGED_BY=other\n')
    assert evidence.udev_policy == 'udev_requests_managed'
    assert evidence.status == 'not_enumerated_unknown'
    assert evidence.permits_activation is False


@pytest.mark.parametrize('state', [10.0, '10', True, -1])
def test_invalid_state_cannot_be_explicit_unmanaged(state):
    evidence = evaluate(nm_managed=False, nm_state=state, dbus_enumerated=True)
    assert evidence.status == 'unknown'
    assert evidence.permits_activation is False


def test_managed_yes_conflicts_with_negative_dbus_enumeration():
    assert evaluate(nm_managed=True, nm_state=20, dbus_enumerated=False).status == 'inconsistent'


def test_invalid_evidence_does_not_echo_private_input():
    evidence = evaluate(nm_managed='private-profile', dbus_enumerated='private-path')
    assert evidence.status == 'unknown'
    assert evidence.nm_enumerated is None and evidence.dbus_enumerated is None
    assert 'private' not in repr(evidence)


def down_sample(matches=None, **change):
    matches=[] if matches is None else matches
    value={"inventories":{
        "GetDevices":{"device_count":3,"matches":[dict(row) for row in matches]},
        "GetAllDevices":{"device_count":3,"matches":[dict(row) for row in matches]},
    },"udev":{"NM_UNMANAGED":"absent","ID_NET_MANAGED_BY":"absent"},
        "ifindex":5,"arphrd":803,"firmware":0,"up":False,"nm_owner_stable":True}
    value.update(change)
    return value


def test_stable_explicit_unmanaged_down_only_observation_is_positive_evidence():
    samples=[down_sample([{"managed":False,"state":10}]) for _ in range(2)]
    result=assess_down_only_samples(samples)
    assert result.status=="nm_explicitly_unmanaged"
    assert result.permits_activation is False


def test_stable_absence_remains_unknown_for_factory_ignored_monitor_vif():
    result=assess_down_only_samples([down_sample(),down_sample()])
    assert result.status=="not_enumerated_unknown"
    assert result.nm_enumerated is False and result.permits_activation is False


@pytest.mark.parametrize("mutate",[
    lambda samples:samples.pop(),
    lambda samples:samples[1].update(ifindex=6),
    lambda samples:samples[1].update(arphrd=801),
    lambda samples:samples[1]["inventories"]["GetDevices"].update(matches=[]),
    lambda samples:samples[0]["inventories"]["GetDevices"].update(matches=[
        {"managed":False,"state":10},{"managed":False,"state":10}]),
    lambda samples:samples[0]["udev"].update(NM_UNMANAGED="private"),
    lambda samples:samples[0].update(up=True),
    lambda samples:samples[0].update(nm_owner_stable=False),
])
def test_down_only_changing_ambiguous_or_malformed_evidence_never_qualifies(mutate):
    samples=[down_sample([{"managed":False,"state":10}]) for _ in range(2)]
    mutate(samples)
    result=assess_down_only_samples(samples)
    assert result.status in ("unknown","inconsistent")
    assert result.permits_activation is False
