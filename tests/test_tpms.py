"""TPMS re-identification: detect TPMS, extract readings, flag returning vehicles."""
from redux.sdr.tpms import is_tpms, tpms_from_record, reidentify, TpmsReading


def test_is_tpms():
    assert is_tpms({"type": "TPMS", "id": "abc"})
    assert is_tpms({"model": "Toyota-TPMS", "id": "1"})
    assert not is_tpms({"model": "Acurite-Tower", "id": "2"})
    assert not is_tpms("nope")


def test_tpms_from_record_extracts_fields():
    r = tpms_from_record({"type": "TPMS", "model": "Ford", "id": "deadbeef",
                          "time": "2026-01-01 12:00:00", "pressure_PSI": 32.0,
                          "temperature_C": 20.0})
    assert r.sensor_id == "deadbeef" and r.model == "Ford"
    assert r.temperature_c == 20.0
    assert abs(r.pressure_kpa - 220.6) < 1.0           # 32 PSI -> ~220.6 kPa


def test_tpms_from_record_needs_id():
    assert tpms_from_record({"type": "TPMS"}) is None
    assert tpms_from_record({"model": "weather", "id": "1"}) is None


def _rec(sid, t, **kw):
    d = {"type": "TPMS", "model": "Ford", "id": sid, "time": float(t)}
    d.update(kw)
    return d


def test_reidentify_flags_returning_vehicle():
    # sensor A seen in a burst at t=0..10, then AGAIN an hour later -> returned
    recs = [_rec("A", 0), _rec("A", 5), _rec("A", 10),
            _rec("A", 3600), _rec("A", 3605),
            _rec("B", 2), _rec("B", 4)]              # B only one pass
    vehicles = {v.sensor_id: v for v in reidentify(recs)}
    assert vehicles["A"].returning and "RETURNED" in vehicles["A"].reason
    assert vehicles["A"].count == 5
    assert not vehicles["B"].returning


def test_reidentify_single_pass_not_returning():
    recs = [_rec("C", 0, pressure_kPa=230.0), _rec("C", 2), _rec("C", 4)]
    v = reidentify(recs)[0]
    assert v.sensor_id == "C" and not v.returning
    assert v.last_pressure_kpa == 230.0


def test_reidentify_sorts_returning_first():
    recs = [_rec("solo", 0),
            _rec("back", 0), _rec("back", 7200)]     # returning
    order = [v.sensor_id for v in reidentify(recs)]
    assert order[0] == "back"
