# redux.geo — NOTES

## Sandbox-tested vs needs real GPS

| Capability | Unit-tested here | Needs hardware / lead wiring |
|------------|------------------|------------------------------|
| Insert / query / dedup | Yes (`:memory:`) | Persistent path on Pi |
| Provenance required | Yes | Mapper must supply real reasons |
| WiGLE CSV shape | Yes (string/headers) | Upload to WiGLE.net is optional/out-of-band |
| Weighted centroid math | Yes (synthetic points) | Real multipath / urban canyon |
| Coverage grid | Yes (synthetic track) | Live gpsd track |
| Live lat/lon from gpsd | **No** | Lead + image services |
| bettercap → Sighting | **No** | Lead (`redux.engine` mapper) |

## Provenance examples (glass-box)

Good:

- `bettercap wifi.ap bssid=… rssi=-62 on wlan1mon`
- `manual wardrive import from capture session 2026-10-06`
- `unit-test synthetic sighting`

Bad (will be rejected or is dishonest):

- empty string
- decorative placeholder with no source

## Query patterns

```python
# All WiFi in a time window
db.query(kind="wifi", since=t0, until=t1)

# Latest N anything
db.query(limit=50)

# Single key
db.get("wifi", "aa:bb:cc:dd:ee:01")
```

## WiGLE export notes

- File starts with `WigleWifi-1.6,appRelease=…` then a column header row.
- `AuthMode` is empty for WiFi until RSN IE is carried on `Sighting` (do not invent).
- BLE rows use `Type=BLE` and `AuthMode=[LE]`.
- Missing GPS → empty lat/lon fields (WiGLE accepts partial rows).

## Centroid caveats

- WiFi RSSI is not a reliable rangefinder; `error_radius_m` is a residual-based
  **rough** figure with a 5 m floor — not survey-grade.
- Prefer many samples from different observer positions; collinear drive-bys
  inflate residual along the path.

## Coverage grid caveats

- Default `cell_deg=0.001` ≈ 111 m at the equator; tighten for walking surveys.
- Bbox is axis-aligned; polar regions need smaller lon cells (not handled yet).
- First arrival in a new city will show mostly gaps — expected.

## Integration checklist (lead)

1. Map bettercap / gpsd events → `Sighting` with real provenance.
2. Open `SightingStore(DEFAULT_DB_PATH)` under the writable data partition.
3. Optional: `export_store` on demand; `estimate_location` per BSSID cluster;
   `CoverageGrid` from the live track.
4. Never call transmit/fire paths from this package — passive data only.
