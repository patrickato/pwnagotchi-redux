"""CSI sensing — the radio as a motion sensor.

Pins the sensing math and the honesty invariant that matters most: no baseline →
UNKNOWN, never a default 'still'. All synthetic, no radio.
"""
import math

import pytest

from redux.sense import (
    CsiFrame, MotionSensor, OccupancyTracker, SenseEngine,
    Sense, Occupancy, estimate_rate_hz, parse_nexmon_csi,
)


WIDTH = 32


def _jit(t, j):
    return 0.08 * (((t * 2654435761 + j * 40503) % 1000) / 1000.0 - 0.5)


def quiet(t):
    return CsiFrame(ts=float(t), amp=tuple(10.0 + _jit(t, j) for j in range(WIDTH)))


def moving(t):
    return CsiFrame(ts=float(t), amp=tuple(10.0 + 3.0 * math.sin(0.8 * t + 0.5 * j) + _jit(t, j)
                                           for j in range(WIDTH)))


# --- the honesty invariant --------------------------------------------------- #

def test_unknown_until_window_full():
    s = MotionSensor(window=16)
    s.calibrate([quiet(t) for t in range(60)])
    r = s.observe(quiet(1000))
    assert r.sense is Sense.UNKNOWN and "warming up" in r.reason


def test_unknown_without_calibration_even_when_warm():
    s = MotionSensor(window=8)
    last = None
    for t in range(20):
        last = s.observe(quiet(t))     # warm, but never calibrated
    assert last.sense is Sense.UNKNOWN
    assert "baseline" in last.reason   # it says WHY, not a fake 'still'


def test_calibration_needs_enough_quiet_data():
    s = MotionSensor(window=16)
    with pytest.raises(ValueError):
        s.calibrate([quiet(t) for t in range(10)])   # fewer than a window+1


# --- detection --------------------------------------------------------------- #

def test_quiet_room_reads_still_after_calibration():
    s = MotionSensor(window=16, sensitivity=5.0)
    s.calibrate([quiet(t) for t in range(80)])
    last = None
    for t in range(200, 260):
        last = s.observe(quiet(t))
    assert last.sense is Sense.STILL


def test_motion_is_detected():
    s = MotionSensor(window=16, sensitivity=5.0)
    s.calibrate([quiet(t) for t in range(80)])
    seen_motion = False
    for t in range(300, 360):
        if s.observe(moving(t)).sense is Sense.MOTION:
            seen_motion = True
    assert seen_motion


def test_reading_is_glass_box():
    s = MotionSensor(window=16)
    s.calibrate([quiet(t) for t in range(80)])
    r = s.observe(moving(400))
    # exposes value, baseline, z and a reason — not just a boolean
    assert r.value >= 0 and r.baseline >= 0 and r.reason
    assert r.z == r.z  # not NaN


# --- occupancy hysteresis ---------------------------------------------------- #

def test_occupancy_sticks_through_brief_stillness_then_goes_vacant():
    eng = SenseEngine.create(window=16, sensitivity=5.0, vacant_after=30)
    eng.calibrate([quiet(t) for t in range(80)])
    for t in range(300, 340):
        eng.observe(moving(t))
    assert eng.occupancy.state is Occupancy.OCCUPIED
    # a long still stretch eventually flips to vacant
    for t in range(1000, 1100):
        eng.observe(quiet(t))
    assert eng.occupancy.state is Occupancy.VACANT


def test_unknown_readings_do_not_move_occupancy():
    occ = OccupancyTracker()
    from redux.sense.csi import SenseReading
    before = occ.state
    occ.update(SenseReading(Sense.UNKNOWN, 0.0, 0.0, 0.0, "warming up"))
    assert occ.state is before is Occupancy.UNKNOWN


# --- engine status ----------------------------------------------------------- #

def test_engine_status_is_honest_before_data():
    eng = SenseEngine.create()
    st = eng.status()
    assert st["available"] is True and st["calibrated"] is False
    assert st["sense"] == "unknown" and st["observed"] == 0


# --- experimental breathing estimator ---------------------------------------- #

def test_estimate_rate_finds_a_clean_periodicity():
    # 0.25 Hz sine sampled at 10 Hz → expect ~0.25 Hz back
    hz = 0.25
    vals = [math.sin(2 * math.pi * hz * (n / 10.0)) for n in range(200)]
    est = estimate_rate_hz(vals, sample_hz=10.0, lo_hz=0.1, hi_hz=0.6)
    assert est is not None and abs(est - hz) < 0.05


def test_estimate_rate_returns_none_on_noise_floor():
    assert estimate_rate_hz([0.0, 0.0, 0.0, 0.0], sample_hz=10.0) is None


# --- fenced hardware parser (structural only) -------------------------------- #

def test_parse_nexmon_csi_structural():
    import struct
    header = b"\x00" * 18
    iq = struct.pack("<hhhh", 3, 4, 6, 8)   # two subcarriers: |3+4j|=5, |6+8j|=10
    frame = parse_nexmon_csi(header + iq)
    assert frame is not None and len(frame.amp) == 2
    assert abs(frame.amp[0] - 5.0) < 1e-6 and abs(frame.amp[1] - 10.0) < 1e-6


def test_parse_nexmon_csi_rejects_malformed():
    assert parse_nexmon_csi(b"\x00" * 3) is None        # shorter than header
    assert parse_nexmon_csi(b"\x00" * 18 + b"\x00") is None  # trailing non-quad
