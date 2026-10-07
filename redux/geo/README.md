# redux.geo — BeastSpatialDB

Unified RF **sighting store** and geospatial helpers for wardrive / recon
maps. Pure data + math in this package; the lead wires live GPS and radio
events at integration. **Do not import `redux.engine` from here.**

## Quick use

```python
from redux.geo import Sighting, SightingStore

with SightingStore(":memory:") as db:  # or DEFAULT_DB_PATH on device
    db.insert(Sighting(
        kind="wifi",
        mac="aa:bb:cc:dd:ee:01",
        ssid="CafeNet",
        lat=37.7749,
        lon=-122.4194,
        rssi=-62,
        channel=6,
        source_radio="wlan0mon",
        ts=1_700_000_000.0,
        provenance="bettercap wifi.ap event → Frame mapper (lead)",
    ))
    rows = db.query(kind="wifi", limit=10)
    for r in rows:
        print(r.mac, r.rssi, r.lat, r.lon, r.provenance)
```

## Default bus path

| Context | Path |
|---------|------|
| On-device store | `/var/lib/redux/geo/sightings.db` (`DEFAULT_DB_PATH`) |
| Tests / CI | `:memory:` or a temp file |

Historical pwnagotchi plugin convention used paths under `/etc/pwnagotchi/…`;
redux keeps **writable** survey data under `/var/lib/redux/geo/` so a
read-only rootfs + captures partition layout stays honest. Lead may symlink
or document a compatibility path at integration.

## Schema (one row per `(kind, mac)`)

| Column | Meaning |
|--------|---------|
| `kind` | `wifi` \| `ble` \| `sdr` \| `other` |
| `mac` | BSSID / BD_ADDR / normalized id (lowercased) |
| `ssid` | Network name when applicable |
| `lat`, `lon` | Best-RSSI sample coordinates (nullable) |
| `rssi` | Best (highest) RSSI seen |
| `channel` | Last/best channel |
| `source_radio` | Interface / radio id that observed |
| `ts` | Latest observation time |
| `first_seen` | Earliest observation time |
| `provenance` | **Required** glass-box string (why this row exists) |

Dedup: stronger RSSI updates position metadata; `first_seen` always keeps the
earliest timestamp.

## Modules

| Module | Role | Branch (if not yet on main) |
|--------|------|-----------------------------|
| `db.py` | `Sighting` + `SightingStore` | `grok/geo-db` |
| `wigle.py` | WiGLE `WigleWifi-1.6` CSV export | `grok/geo-wigle` |
| `estimate.py` | RSSI weighted-centroid location | `grok/geo-estimate` |
| `coverage.py` | Survey coverage / gap grid | `grok/geo-coverage` |
| `README.md` / `NOTES.md` | This doc set | `grok/geo-docs` |

## Tests

Hardware-free: `tests/test_geo_*.py` (insert/query/dedup, CSV headers,
centroid math, grid gaps). Anything that needs a real GPS fix or radio is an
integration gate owned by the lead — not asserted in these unit tests.
