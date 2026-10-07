"""Capability graph — the keystone that unifies packs / radios / GPS / SDR / the
firing gate under one vocabulary. Pure logic, no hardware."""
from redux.core import Cap, CapState, Provider, CapabilityGraph


def _graph_with_gps_alternates(gps_present=True, dr_present=True):
    g = CapabilityGraph()
    # two providers of LOCATION_POSITION — a real GPS and dead-reckoning
    g.register(Provider.of("gps", provides=[Cap.LOCATION_POSITION],
                           present=gps_present, reason="USB GPS has a 3D fix" if gps_present
                           else "no gpsd device present"))
    g.register(Provider.of("dead_reckoning", provides=[Cap.LOCATION_POSITION],
                           present=dr_present, reason="estimating from last fix + motion"))
    # a consumer that hard-requires a position
    g.register(Provider.of("coverage_grid", requires=[Cap.LOCATION_POSITION],
                           present=True, reason="ready"))
    return g


def test_active_provider_prefers_registration_order():
    g = _graph_with_gps_alternates()
    assert g.active_provider(Cap.LOCATION_POSITION).name == "gps"       # registered first
    # if GPS is absent, the present alternate wins
    g2 = _graph_with_gps_alternates(gps_present=False)
    assert g2.active_provider(Cap.LOCATION_POSITION).name == "dead_reckoning"


def test_unknown_capability_is_unknown_not_missing():
    g = CapabilityGraph()
    g.register(Provider.of("x", requires=[Cap.SDR_RX]))
    state, reason = g.resolve("x")[Cap.SDR_RX.value]
    assert state is CapState.UNKNOWN and "no registered provider" in reason


def test_requirement_satisfied_when_a_present_provider_exists():
    g = _graph_with_gps_alternates()
    state, reason = g.resolve("coverage_grid")[Cap.LOCATION_POSITION.value]
    assert state is CapState.SATISFIED and "gps" in reason


def test_requirement_missing_when_no_provider_present():
    g = _graph_with_gps_alternates(gps_present=False, dr_present=False)
    state, reason = g.resolve("coverage_grid")[Cap.LOCATION_POSITION.value]
    assert state is CapState.MISSING
    assert "no present provider" in reason


def test_hardware_absent_distinguished_from_plain_missing():
    g = CapabilityGraph()
    g.register(Provider.of("rtl_sdr", provides=[Cap.SDR_RX], present=False,
                           reason="no SDR hardware present"))
    g.register(Provider.of("adsb", requires=[Cap.SDR_RX]))
    state, _ = g.resolve("adsb")[Cap.SDR_RX.value]
    assert state is CapState.HARDWARE_ABSENT


def test_conflict_detected_when_both_present():
    g = CapabilityGraph()
    g.register(Provider.of("uplink", provides=[Cap.RADIO_WIFI_UPLINK], present=True, reason="wlan0 client"))
    # a monitor role that cannot coexist with an uplink on the same single radio
    g.register(Provider.of("monitor", provides=[Cap.RADIO_WIFI_MONITOR],
                           conflicts=[Cap.RADIO_WIFI_UPLINK], present=True, reason="wlan0 monitor"))
    state, reason = g.resolve("monitor")[Cap.RADIO_WIFI_UPLINK.value]
    assert state is CapState.CONFLICT and "conflicts" in reason


def test_requires_any_satisfied_by_one_alternative():
    g = CapabilityGraph()
    g.register(Provider.of("gps", provides=[Cap.LOCATION_POSITION], present=True, reason="fix"))
    g.register(Provider.of("dossier", requires_any=[[Cap.LOCATION_POSITION, Cap.NETWORK_INTERNET]]))
    res = g.resolve("dossier")
    key = next(k for k in res if k.startswith("any("))
    assert res[key][0] is CapState.SATISFIED


def test_blast_radius_reports_alternates():
    # GPS + dead-reckoning both present → losing GPS still leaves an alternate
    g = _graph_with_gps_alternates(gps_present=True, dr_present=True)
    br = g.blast_radius(Cap.LOCATION_POSITION)
    assert br["active_provider"] == "gps"
    assert br["present_alternates"] == ["dead_reckoning"]
    assert br["affected"] == [{"consumer": "coverage_grid", "has_alternate": True}]
    assert "1 have a present alternate" in br["reason"] or "have a present alternate" in br["reason"]


def test_blast_radius_flags_no_alternate():
    g = CapabilityGraph()
    g.register(Provider.of("gps", provides=[Cap.LOCATION_POSITION], present=True, reason="fix"))
    g.register(Provider.of("coverage_grid", requires=[Cap.LOCATION_POSITION]))
    br = g.blast_radius(Cap.LOCATION_POSITION)
    assert br["present_alternates"] == []
    assert br["affected"][0]["has_alternate"] is False
    assert "1 have none" in br["reason"]


def test_explain_is_glass_box():
    g = _graph_with_gps_alternates()
    ex = g.explain(Cap.LOCATION_POSITION)
    assert ex["available"] is True
    assert ex["active_provider"] == "gps"
    assert "coverage_grid" in ex["consumers"]
    names = {c["name"] for c in ex["candidates"]}
    assert names == {"gps", "dead_reckoning"}


def test_firing_gate_is_just_a_consumer():
    # the offensive gate requires monitor capability like any other consumer
    g = CapabilityGraph()
    g.register(Provider.of("wifi_monitor", provides=[Cap.RADIO_WIFI_MONITOR],
                           present=False, reason="no monitor-capable radio present"))
    g.register(Provider.of("deauth_gate", requires=[Cap.RADIO_WIFI_MONITOR]))
    assert g.is_satisfied("deauth_gate") is False
    state, _ = g.resolve("deauth_gate")[Cap.RADIO_WIFI_MONITOR.value]
    assert state in (CapState.MISSING, CapState.HARDWARE_ABSENT)


def test_duplicate_provider_name_rejected():
    g = CapabilityGraph()
    g.register(Provider.of("gps", provides=[Cap.LOCATION_POSITION]))
    try:
        g.register(Provider.of("gps", provides=[Cap.LOCATION_POSITION]))
        assert False, "expected ValueError"
    except ValueError:
        pass
