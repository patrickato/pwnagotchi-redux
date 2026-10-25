"""gpsd client: TPV parsing, fix filtering, and the position provider (no socket)."""
from redux.geo.gpsd import (
    parse_tpv, fixes_from_lines, GpsdClient, gpsd_position_provider,
)


# ---- TPV parsing --------------------------------------------------------- #

def test_parse_tpv_3d_fix():
    fix = parse_tpv({"class": "TPV", "mode": 3, "lat": 40.1, "lon": -80.2})
    assert fix and fix.lat == 40.1 and fix.lon == -80.2


def test_parse_tpv_rejects_no_fix():
    assert parse_tpv({"class": "TPV", "mode": 1}) is None       # mode 1 = no fix
    assert parse_tpv({"class": "TPV", "mode": 0}) is None
    assert parse_tpv({"class": "SKY", "mode": 3}) is None        # not a TPV


def test_parse_tpv_rejects_garbage_coords():
    assert parse_tpv({"class": "TPV", "mode": 3, "lat": 999, "lon": 0}) is None
    assert parse_tpv({"class": "TPV", "mode": 3, "lat": "x", "lon": 0}) is None
    assert parse_tpv({"class": "TPV", "mode": 2, "lat": None, "lon": None}) is None


# ---- stream parsing ------------------------------------------------------ #

GPSD_STREAM = [
    '{"class":"VERSION","release":"3.22"}',
    '{"class":"DEVICES","devices":[]}',
    '{"class":"TPV","mode":1}',                                  # no fix yet
    'not json',
    '{"class":"TPV","mode":3,"lat":37.77,"lon":-122.41}',        # first real fix
    '{"class":"TPV","mode":3,"lat":37.78,"lon":-122.42}',
]


def test_fixes_from_lines_skips_until_real_fix():
    fixes = list(fixes_from_lines(GPSD_STREAM))
    assert len(fixes) == 2
    assert fixes[0].lat == 37.77


# ---- client + provider (injected line source) --------------------------- #

def test_client_poll_returns_first_fix():
    c = GpsdClient(line_source=lambda h, p, t: iter(GPSD_STREAM))
    fix = c.poll_fix()
    assert fix and fix.lat == 37.77


def test_client_poll_none_when_no_fix():
    c = GpsdClient(line_source=lambda h, p, t: iter(['{"class":"TPV","mode":1}']))
    assert c.poll_fix() is None


def test_client_poll_none_when_unreachable():
    def _boom(h, p, t):
        raise OSError("connection refused")
    assert GpsdClient(line_source=_boom).poll_fix() is None


def test_position_provider_shape():
    prov = gpsd_position_provider(line_source=lambda h, p, t: iter(GPSD_STREAM))
    assert prov() == (37.77, -122.41)
    empty = gpsd_position_provider(line_source=lambda h, p, t: iter([]))
    assert empty() is None
