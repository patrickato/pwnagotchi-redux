"""GPS NMEA source: the missing position_provider that lights up geo features."""
from redux.geo.gps import (parse_nmea_line, NmeaTracker, pump_lines,
                           serial_position_provider, GpsFix)

# Real-ish sentences (valid checksums).
GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
NOFIX_GGA = "$GPGGA,123519,,,,,0,00,,,,,,,*66"
VOID_RMC = "$GPRMC,123519,V,,,,,,,230394,,*4D"


def test_parse_gga_to_decimal_degrees():
    fix = parse_nmea_line(GGA)
    assert fix is not None
    assert abs(fix.lat - 48.1173) < 1e-3     # 4807.038 -> 48 + 7.038/60
    assert abs(fix.lon - 11.5167) < 1e-3


def test_parse_rmc_and_southern_western_hemispheres():
    assert parse_nmea_line(RMC) is not None
    # a clean southern/western GGA (no checksum -> tolerated): Sydney-ish
    s = parse_nmea_line("$GPGGA,0,3345.000,S,15112.000,W,1,04,,,,,,,")
    assert s is not None and s.lat < 0 and s.lon < 0
    assert abs(s.lat - -33.75) < 1e-6 and abs(s.lon - -151.2) < 1e-6


def test_no_fix_and_void_status_yield_nothing():
    assert parse_nmea_line(NOFIX_GGA) is None
    assert parse_nmea_line(VOID_RMC) is None


def test_bad_checksum_is_rejected():
    assert parse_nmea_line(GGA[:-2] + "00") is None   # corrupt the checksum
    assert parse_nmea_line("garbage") is None
    assert parse_nmea_line("$GNGLL,only,three") is None


def test_tracker_holds_latest_fix_and_acts_as_provider():
    t = NmeaTracker()
    assert t() is None                       # no fix yet
    t.feed(VOID_RMC)                         # invalid -> still none
    assert t() is None
    t.feed(GGA)
    assert t() is not None and abs(t()[0] - 48.1173) < 1e-3
    t.feed(NOFIX_GGA)                        # a later no-fix doesn't erase the last good one
    assert t() is not None


def test_pump_lines_feeds_a_tracker_from_bytes_or_str():
    t = pump_lines([GGA.encode(), b"junk", RMC.encode()], NmeaTracker())
    assert t() is not None and abs(t()[0] - 48.1173) < 1e-3


def test_serial_provider_pumps_an_injected_stream():
    # inject a fake serial stream (bytes lines) — no hardware, no pyserial
    lines = [GGA.encode(), b"junk", RMC.encode()]
    prov = serial_position_provider("/dev/fake", open_stream=lambda d, b: iter(lines))
    import time
    for _ in range(100):             # background thread consumes the finite stream
        if prov() is not None:
            break
        time.sleep(0.01)
    assert prov() is not None


def test_device_absent_stays_none_quietly():
    def _boom(dev, baud):
        raise OSError("no such device")
    prov = serial_position_provider("/dev/nope", open_stream=_boom)
    assert prov() is None                    # never raises; just no fix


def test_live_runtime_gps_wiring_is_best_effort(monkeypatch):
    from redux.core.live_runtime import _gps_from_env
    monkeypatch.delenv("REDUX_GPS", raising=False)
    assert _gps_from_env() is None                       # unset -> no provider
    monkeypatch.setenv("REDUX_GPS", "/dev/nonexistent-gps")
    prov = _gps_from_env()
    assert prov is not None and prov() is None           # device absent -> no fix, no crash


def test_stale_fix_is_dropped_not_served_forever():
    clk = [0.0]
    t = NmeaTracker(max_age_s=5.0, clock=lambda: clk[0])
    t.feed(GGA)
    assert t() is not None            # fresh
    clk[0] = 4.0
    assert t() is not None            # still within window
    clk[0] = 6.0
    assert t() is None                # aged out -> un-located, never a stale coordinate


def test_nmea_rejects_nonfinite_coord_without_crashing():
    """A nan/inf coordinate field must return None, never raise — one bad sentence
    must not kill the GPS reader thread. (No checksum here so the isfinite guard,
    not checksum rejection, is what's exercised.)"""
    from redux.geo.gps import parse_nmea_line
    assert parse_nmea_line("$GPGGA,123519,nan,N,01131.000,E,1,08,0.9,545.4,M,,,") is None
    assert parse_nmea_line("$GPGGA,123519,4807.038,N,inf,E,1,08,0.9,545.4,M,,,") is None
    # a well-formed line (no checksum, which the parser tolerates) still parses
    assert parse_nmea_line("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,,,") is not None
