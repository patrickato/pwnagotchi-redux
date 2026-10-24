"""POCSAG/ACARS decoders and rtl_power spectrum analysis — no radio needed."""
from redux.sdr import (
    parse_pocsag_line, pocsag_to_sighting, ingest_pocsag,
    acars_to_sighting, ingest_acars, parse_rtl_power,
)
from redux.geo import SightingStore


# ---- POCSAG -------------------------------------------------------------- #

def test_parse_pocsag_alpha():
    p = parse_pocsag_line("POCSAG1200: Address: 1234567  Function: 3  Alpha:   hello world")
    assert p["protocol"] == "POCSAG1200" and p["address"] == "1234567"
    assert p["function"] == 3 and p["msg_kind"] == "alpha"
    assert p["message"] == "hello world"


def test_parse_pocsag_numeric():
    p = parse_pocsag_line("POCSAG512: Address:  999999  Function: 0  Numeric: 12345")
    assert p["protocol"] == "POCSAG512" and p["address"] == "999999"
    assert p["message"] == "12345"


def test_parse_pocsag_rejects_non_pocsag():
    assert parse_pocsag_line("FLEX|...|something") is None
    assert parse_pocsag_line("") is None


def test_pocsag_sighting_keeps_capcode_drops_message():
    p = parse_pocsag_line("POCSAG1200: Address: 1234567  Function: 3  Alpha: SECRET PATIENT INFO")
    s = pocsag_to_sighting(p, position=(40.0, -80.0), ts=123.0)
    assert s.kind == "pocsag" and s.mac == "pocsag_1234567" and s.ssid == "POCSAG1200"
    assert s.lat == 40.0 and s.lon == -80.0 and s.ts == 123.0
    # the message body must NOT appear anywhere in the persisted sighting
    blob = f"{s.mac} {s.ssid} {s.provenance}"
    assert "SECRET" not in blob and "PATIENT" not in blob


def test_ingest_pocsag_batches_and_skips_noise():
    store = SightingStore(":memory:")
    lines = [
        "POCSAG1200: Address: 1111111  Function: 0  Alpha: a",
        "garbage line that is not pocsag",
        "POCSAG512: Address: 2222222  Function: 1  Numeric: 42",
    ]
    n = ingest_pocsag(lines, store, position=(1.0, 2.0), now=5.0)
    assert n == 2
    kinds = {s.kind for s in store.query()}
    assert kinds == {"pocsag"}


# ---- ACARS --------------------------------------------------------------- #

def test_acars_sighting_by_tail_drops_text():
    rec = {"timestamp": 1700000000.0, "tail": "N12345", "flight": "UA0123",
           "text": "CREW MESSAGE DO NOT STORE", "freq": 131.55}
    s = acars_to_sighting(rec, position=(10.0, 20.0))
    assert s.kind == "acars" and s.mac == "acars_n12345" and s.ssid == "UA0123"
    assert s.lat == 10.0 and s.ts == 1700000000.0
    assert "CREW" not in f"{s.mac} {s.ssid}"


def test_acars_needs_an_identity():
    assert acars_to_sighting({"text": "x"}) is None


def test_ingest_acars_batches():
    store = SightingStore(":memory:")
    recs = [{"tail": "N1"}, {"flight": "AA9"}, {"text": "no id"}]
    assert ingest_acars(recs, store) == 2


# ---- rtl_power spectrum -------------------------------------------------- #

def _sweep_rows():
    # 10 bins from 433.0 MHz, step 25 kHz, mostly -50 dB floor with a spike at bin 4
    floor = [-50.0] * 10
    floor[4] = -30.0            # a strong signal ~ +20 dB above floor
    db = ",".join(str(x) for x in floor)
    return [f"2026-01-01, 12:00:00, 433000000, 433250000, 25000, 100, {db}"]


def test_parse_rtl_power_finds_peak_and_floor():
    sweep = parse_rtl_power(_sweep_rows(), peak_threshold_db=8.0)
    assert sweep.noise_floor_db == -50.0
    assert len(sweep.peaks) == 1
    pk = sweep.peaks[0]
    assert abs(pk.freq_hz - (433000000 + 4 * 25000)) < 1.0
    assert pk.db == -30.0 and pk.above_floor_db == 20.0


def test_parse_rtl_power_merges_adjacent_bins_into_one_peak():
    # two adjacent strong bins = one signal, not two peaks
    vals = [-60.0] * 8
    vals[3] = -20.0; vals[4] = -22.0
    row = f"d, t, 100000000, 100175000, 25000, 50, {','.join(str(v) for v in vals)}"
    sweep = parse_rtl_power([row])
    assert len(sweep.peaks) == 1
    assert sweep.peaks[0].db == -20.0          # the stronger of the adjacent run


def test_parse_rtl_power_empty_and_malformed():
    sweep = parse_rtl_power(["", "not,enough,cols", "a,b,c,d,e,f,g"])
    assert sweep.bins == () and sweep.peaks == ()


def test_parse_rtl_power_quiet_band_has_no_peaks():
    row = "d, t, 90000000, 90100000, 25000, 50, -45.0,-45.1,-44.9,-45.0,-45.2"
    sweep = parse_rtl_power([row], peak_threshold_db=8.0)
    assert sweep.peaks == ()                    # nothing meaningfully above the floor
