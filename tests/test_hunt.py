"""RSSI-gradient fox-hunt — trend, band, and position estimate.

Honest checks: trend is relative (warmer/colder/steady), distance is a coarse band
(never meters), and the estimate/bearing only appear with enough GPS fixes.
"""
import math

from redux.hunt import FoxHunt, HuntObservation, bearing_deg, compass


def _feed(fh, rssis, pos=None):
    st = None
    for r in rssis:
        st = fh.observe(HuntObservation(rssi=r,
                                        lat=(pos[0] if pos else None),
                                        lon=(pos[1] if pos else None)))
    return st


# --- trend ------------------------------------------------------------------- #

def test_unknown_on_first_sample():
    assert FoxHunt("t").observe(HuntObservation(rssi=-70)).trend == "unknown"


def test_rising_rssi_is_warmer():
    st = _feed(FoxHunt("t"), [-90, -84, -78, -70, -62])
    assert st.trend == "warmer"


def test_falling_rssi_is_colder():
    st = _feed(FoxHunt("t"), [-55, -62, -70, -78, -86])
    assert st.trend == "colder"


def test_flat_rssi_is_steady():
    st = _feed(FoxHunt("t"), [-70, -71, -70, -69, -70])
    assert st.trend == "steady"


# --- band -------------------------------------------------------------------- #

def test_band_tracks_proximity():
    assert FoxHunt("t").observe(HuntObservation(rssi=-45)).band == "on top of it"
    assert FoxHunt("t").observe(HuntObservation(rssi=-66)).band == "close"
    assert FoxHunt("t").observe(HuntObservation(rssi=-95)).band == "far"


def test_best_rssi_tracks_max():
    st = _feed(FoxHunt("t"), [-80, -60, -75, -90])
    assert st.best_rssi == -60


# --- estimate + bearing ------------------------------------------------------ #

def test_estimate_requires_two_gps_samples():
    fh = FoxHunt("t")
    st1 = fh.observe(HuntObservation(rssi=-70, lat=45.0, lon=-93.0))
    assert st1.estimate is None and st1.bearing_deg is None     # only one fix
    st2 = fh.observe(HuntObservation(rssi=-60, lat=45.001, lon=-93.001))
    assert st2.estimate is not None
    assert "lat" in st2.estimate and st2.estimate["error_radius_m"] >= 5.0
    assert st2.bearing_deg is not None
    assert st2.bearing_compass in ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def test_no_estimate_without_gps():
    st = _feed(FoxHunt("t"), [-80, -70, -60])   # no positions
    assert st.estimate is None and st.bearing_deg is None


# --- geometry helpers -------------------------------------------------------- #

def test_bearing_cardinals():
    assert abs(bearing_deg(0, 0, 1, 0) - 0) < 1        # due north
    assert abs(bearing_deg(0, 0, 0, 1) - 90) < 1       # due east
    assert compass(0) == "N" and compass(90) == "E" and compass(180) == "S"
