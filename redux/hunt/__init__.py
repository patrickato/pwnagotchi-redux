"""redux.hunt — RSSI-gradient fox-hunt.

"Warmer/colder," a coarse proximity band, and a weighted-centroid position + bearing
to walk onto a scoped target, from the RSSI the sighting store already logs. No FTM,
no extra hardware. Honest: the trend is relative and robust; distance is a coarse
band (never fake meters); the position estimate and bearing appear only once there
are enough GPS-tagged samples to form them.
"""
from .hunt import (
    FoxHunt, HuntObservation, HuntState, bearing_deg, compass,
)

__all__ = ["FoxHunt", "HuntObservation", "HuntState", "bearing_deg", "compass"]
