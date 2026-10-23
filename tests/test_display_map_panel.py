"""Hardware-free tests for the native TFT moving-map panel."""
from __future__ import annotations

import math

from redux.display import map_panel


# --- a tiny draw recorder (no Pillow needed) --------------------------------- #

class FakeDraw:
    def __init__(self):
        self.calls = []

    def text(self, xy, s, **kw):
        self.calls.append(("text", xy, s))

    def line(self, xy, **kw):
        self.calls.append(("line", xy))

    def ellipse(self, xy, **kw):
        self.calls.append(("ellipse", xy))

    def rectangle(self, xy, **kw):
        self.calls.append(("rectangle", xy))


def _font(size, bold=False):
    return ("font", size, bold)


_INKS = dict(font=_font, ink=(255, 255, 255), subtle=(120, 140, 150),
             teal=(0, 200, 180), amber=(240, 180, 40), blue=(60, 140, 240))


def _texts(d):
    return " ".join(c[2] for c in d.calls if c[0] == "text")


# --- validation (pure) ------------------------------------------------------- #

def test_coord_rejects_nonreal_and_out_of_range():
    assert map_panel._coord(37.0, -122.0) == (37.0, -122.0)
    assert map_panel._coord(None, -122.0) is None
    assert map_panel._coord(float("nan"), 0.0) is None
    assert map_panel._coord(91.0, 0.0) is None
    assert map_panel._coord(0.0, 200.0) is None


def test_map_view_filters_bad_points_and_reports_empty():
    assert map_view_empty(map_panel.map_view(None))
    assert map_view_empty(map_panel.map_view({"own": None, "track": [], "pins": []}))
    v = map_panel.map_view({"own": [37.0, -122.0],
                            "track": [[37.0, -122.0], [None, 1], [37.1, -122.1]],
                            "pins": [[37.05, -122.05, -50, "ap"], ["x", "y"], [37.06, -122.06]]})
    assert v["empty"] is False
    assert v["own"] == (37.0, -122.0)
    assert v["track"] == [(37.0, -122.0), (37.1, -122.1)]   # bad middle dropped
    assert len(v["pins"]) == 2                               # the ("x","y") pin dropped
    assert v["pins"][0][2] == -50 and v["pins"][0][3] == "ap"
    assert v["pins"][1][2] is None                            # missing rssi -> None


def map_view_empty(v):
    return v.get("empty") is True


# --- projection (pure) ------------------------------------------------------- #

def test_project_corners_map_north_up():
    bbox = map_panel.BBox(37.0, 37.1, -122.1, -122.0)  # lat range, lon range
    region = (0, 0, 100, 100)
    # max latitude -> top (small y); min latitude -> bottom (large y)
    _, y_north = map_panel.project(37.1, -122.05, bbox, region)
    _, y_south = map_panel.project(37.0, -122.05, bbox, region)
    assert y_north < y_south
    # max lon -> right (large x); min lon -> left
    x_east, _ = map_panel.project(37.05, -122.0, bbox, region)
    x_west, _ = map_panel.project(37.05, -122.1, bbox, region)
    assert x_east > x_west


def test_project_clamps_into_region():
    bbox = map_panel.BBox(37.0, 37.1, -122.1, -122.0)
    region = (10, 20, 90, 80)
    x, y = map_panel.project(90.0, 179.0, bbox, region)  # way outside bbox
    assert 10 <= x <= 90 and 20 <= y <= 80


def test_bbox_padded_enforces_min_span():
    bb = map_panel.bounds([(37.0, -122.0)])            # single point -> zero span
    padded = bb.padded()
    assert padded.max_lat - padded.min_lat >= map_panel._MIN_SPAN_DEG
    assert padded.max_lon - padded.min_lon >= map_panel._MIN_SPAN_DEG


# --- drawing (fake recorder) ------------------------------------------------- #

def test_draw_map_empty_state_is_honest():
    d = FakeDraw()
    n = map_panel.draw_map(d, {"own": None, "track": [], "pins": []}, **_INKS)
    assert n == 0
    t = _texts(d)
    assert "NO MAP FIX" in t and "real position" in t
    # an honest empty map draws no track/pins
    assert not any(c[0] in ("line",) for c in d.calls if c[1] != ())


def test_draw_map_plots_track_pins_and_own():
    d = FakeDraw()
    view = {"own": [37.00, -122.00],
            "track": [[37.00, -122.00], [37.01, -122.00], [37.02, -122.01]],
            "pins": [[37.015, -122.005, -45, "ap"], [37.018, -122.004, -85, "ble"]]}
    n = map_panel.draw_map(d, view, **_INKS)
    assert n == 3  # 2 pins + own
    kinds = [c[0] for c in d.calls]
    assert "line" in kinds       # the track polyline (and the own crosshair)
    assert "ellipse" in kinds    # the ap pin
    assert "rectangle" in kinds  # the ble pin (square) + the map border
    assert "PINS" in _texts(d)


def test_build_map_view_from_sighting_rows():
    from redux.geo import Sighting
    rows = [
        Sighting(kind="wifi", mac="aa:bb:cc:dd:ee:01", lat=37.0, lon=-122.0, rssi=-50, provenance="t"),
        Sighting(kind="ble", mac="aa:bb:cc:dd:ee:02", lat=37.1, lon=-122.1, rssi=-70, provenance="t"),
        Sighting(kind="wifi", mac="aa:bb:cc:dd:ee:03", lat=None, lon=None, rssi=-60, provenance="t"),  # no GPS -> dropped
    ]
    view = map_panel.build_map_view(rows, own=(37.05, -122.05), track=[(37.0, -122.0), (37.05, -122.05)])
    assert view["own"] == [37.05, -122.05]
    assert len(view["track"]) == 2
    assert len(view["pins"]) == 2                      # the lat/lon-less row dropped
    kinds = {p[3] for p in view["pins"]}
    assert kinds == {"ap", "ble"}
    # and it round-trips through the validator cleanly
    v = map_panel.map_view(view)
    assert v["empty"] is False and len(v["pins"]) == 2


def test_build_map_view_empty_when_no_real_data():
    view = map_panel.build_map_view([], own=None, track=None)
    assert map_panel.map_view(view)["empty"] is True


def test_pin_color_buckets_by_rssi():
    strong, mid, weak = "S", "M", "W"
    assert map_panel._pin_color(-40, strong, mid, weak) == strong
    assert map_panel._pin_color(-65, strong, mid, weak) == mid
    assert map_panel._pin_color(-90, strong, mid, weak) == weak
    assert map_panel._pin_color(None, strong, mid, weak) == weak
