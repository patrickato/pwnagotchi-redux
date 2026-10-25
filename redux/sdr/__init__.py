"""SDR passive-sensing layer.

Plug a receive-only RTL-SDR and redux gains a whole spectrum beyond WiFi/BLE —
all passive, dead-center in the authorized/passive ethos. This package ingests
the output of the standard SDR tools and geo-tags each detection into the SAME
sightings store, so ISM sensors and aircraft show up in the SAME Field Dex:

  - rtl_433  → ISM-band devices (weather stations, TPMS, meters, remotes)
  - dump1090 → ADS-B aircraft (they broadcast their own position)

Readers are injected (a recorded sample stream in tests; a live subprocess/socket
on hardware), so parsing + geo-tagging is fully testable with no radio. Nothing
is invented: a record with no usable id is skipped.
"""
from .ingest import (
    rtl433_to_sighting,
    adsb_to_sighting,
    ingest_rtl433,
    ingest_adsb,
)
from .decoders import (
    parse_pocsag_line,
    pocsag_to_sighting,
    ingest_pocsag,
    acars_to_sighting,
    ingest_acars,
)
from .spectrum import parse_rtl_power, SpectrumSweep, Peak, Bin

__all__ = [
    "rtl433_to_sighting",
    "adsb_to_sighting",
    "ingest_rtl433",
    "ingest_adsb",
    "parse_pocsag_line",
    "pocsag_to_sighting",
    "ingest_pocsag",
    "acars_to_sighting",
    "ingest_acars",
    "parse_rtl_power", "SpectrumSweep", "Peak", "Bin",
]
