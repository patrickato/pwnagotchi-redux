"""redux.geo — SpatialDB and geospatial helpers.

Sighting store + WiGLE export/queue + location estimate + coverage grid + self-locate.
No redux.engine import — lead wires live GPS at integration.
"""
from __future__ import annotations

from redux.geo.coverage import CoverageGrid, GridCell, coverage_from_track
from redux.geo.db import DEFAULT_DB_PATH, Sighting, SightingStore
from redux.geo.dead_reckoning import Fix, fill_gaps
from redux.geo.estimate import LocationEstimate, estimate_from_coords, estimate_location
from redux.geo.export_tracks import export_store_gpx, export_store_kml, to_gpx, to_kml
from redux.geo.geofence import inside_authorized_area, point_in_geojson_polygon
from redux.geo.geohash import group_by_cell
from redux.geo.kismet_import import import_kismetdb
from redux.geo.return_signal import ReturnToSignal, return_to_signal
from redux.geo.self_locate import VisibleAP, self_locate, self_locate_from_scan
from redux.geo.wigle import (
    WIGLE_COLUMNS,
    channel_to_frequency_mhz,
    export_store,
    kismetdb_to_wiglecsv_dump,
    write_wigle_csv,
)
from redux.geo.wigle_queue import WigleExportQueue

__all__ = [
    "CoverageGrid",
    "DEFAULT_DB_PATH",
    "Fix",
    "GridCell",
    "LocationEstimate",
    "ReturnToSignal",
    "Sighting",
    "SightingStore",
    "VisibleAP",
    "WIGLE_COLUMNS",
    "WigleExportQueue",
    "channel_to_frequency_mhz",
    "coverage_from_track",
    "estimate_from_coords",
    "estimate_location",
    "export_store",
    "export_store_gpx",
    "export_store_kml",
    "fill_gaps",
    "group_by_cell",
    "import_kismetdb",
    "inside_authorized_area",
    "kismetdb_to_wiglecsv_dump",
    "point_in_geojson_polygon",
    "return_to_signal",
    "self_locate",
    "self_locate_from_scan",
    "to_gpx",
    "to_kml",
    "write_wigle_csv",
]
