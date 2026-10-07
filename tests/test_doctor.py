"""Doctor — headless self-diagnosis over the capability graph + governor + scope."""
from redux.core import (
    Doctor, DoctorInputs, Status, CapabilityGraph, Cap, Provider, Scope,
    Governor, Reading,
)


def _graph(monitor_present=True, location_present=True):
    g = CapabilityGraph()
    g.register(Provider.of("wifi-monitor", provides=[Cap.RADIO_WIFI_MONITOR],
                           present=monitor_present, reason="wlan1 monitor" if monitor_present else "no monitor radio"))
    g.register(Provider.of("position", provides=[Cap.LOCATION_POSITION],
                           present=location_present, reason="GPS fix" if location_present else "no fix"))
    g.register(Provider.of("capture", requires=[Cap.RADIO_WIFI_MONITOR]))
    g.register(Provider.of("coverage-map", requires=[Cap.LOCATION_POSITION]))
    return g


def test_all_ok_report():
    inp = DoctorInputs(graph=_graph(), governor=Governor().evaluate(Reading(cpu_temp_c=40.0), now=0),
                       scope=Scope(), detector_count=13, sightings=0)
    rep = Doctor().report(inp)
    # scope empty -> ATTENTION is the worst known status
    assert rep["overall"] in ("ok", "attention")
    areas = {f["area"]: f["status"] for f in rep["findings"]}
    assert areas["capture radio"] == "ok"
    assert areas["detectors"] == "ok"
    assert rep["coverage"]["not_assessed"] == []


def test_missing_capture_radio_is_action_required_with_blast_radius():
    inp = DoctorInputs(graph=_graph(monitor_present=False), detector_count=13)
    rep = Doctor().report(inp)
    cap = next(f for f in rep["findings"] if f["area"] == "capture radio")
    assert cap["status"] == "action"
    assert "affects" in cap["reason"]              # blast-radius surfaced
    assert rep["overall"] == "action"


def test_thermal_maps_governor_mode():
    hot = DoctorInputs(governor=Governor().evaluate(Reading(cpu_temp_c=82.0), now=0))
    f = next(x for x in Doctor().report(hot)["findings"] if x["area"] == "thermal/power")
    assert f["status"] == "action" and "Shedding load" in f["summary"]


def test_unknown_areas_are_coverage_gaps_not_passes():
    # no governor, no graph, no scope, no detector count -> everything unknown
    rep = Doctor().report(DoctorInputs())
    assert rep["overall"] == "unknown"
    gaps = rep["coverage"]["not_assessed"]
    assert "thermal/power" in gaps and "scope" in gaps and "detectors" in gaps
    # a clean report never claims to have checked what it couldn't
    assert all(f["status"] == "unknown" for f in rep["findings"])


def test_scope_armed_is_ok():
    s = Scope(); s.add("aa:bb:cc:dd:ee:ff", job="acme")
    f = next(x for x in Doctor().report(DoctorInputs(scope=s))["findings"] if x["area"] == "scope")
    assert f["status"] == "ok" and "acme" in f["summary"]


def test_location_unavailable_is_attention_not_action():
    inp = DoctorInputs(graph=_graph(location_present=False))
    f = next(x for x in Doctor().report(inp)["findings"] if x["area"] == "location")
    assert f["status"] == "attention"    # useful but not required -> not action


def test_augur_doctor_report_from_live_state():
    from redux.core import Augur
    from redux.radio import Radio, Intent
    alfa = Radio("wlan1", bands=frozenset({"2.4", "5"}), monitor=True, inject=True, driver="mt76x2u")
    bc = Augur([alfa], intent=Intent.HUNT)
    rep = bc.doctor_report()
    areas = {f["area"]: f["status"] for f in rep["findings"]}
    assert areas["capture radio"] == "ok"       # the Alfa supports monitor
    assert areas["detectors"] == "ok"           # 13 detectors wired
    assert areas["scope"] == "attention"        # empty scope by default
