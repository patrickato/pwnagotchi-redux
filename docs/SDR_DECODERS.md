# More SDR decoders: POCSAG, ACARS, and spectrum sweep (`redux sdr`)

Plug a receive-only RTL-SDR and the box already ingests **rtl_433** (ISM:
weather stations, TPMS, meters, remotes) and **dump1090** (ADS-B aircraft) into
the sighting store. This adds three more passive sources — all dead-center in
the authorized/passive ethos (receive-only, transmits nothing):

- **POCSAG pagers** (via `multimon-ng`)
- **ACARS aircraft messaging** (via `acarsdec`)
- **rtl_power spectrum sweeps** — "what's actually on the air"

## Privacy: identity, not content

A pager message or an ACARS free-text field can carry personal or operational
content (hospital pagers, crew messages). We treat these the same way we treat
an ISM sensor id or an ADS-B hex: the sighting records **only the identity heard
here** — the pager capcode, the aircraft tail — and the message body is
**dropped before anything is persisted**. `parse_pocsag_line()` still returns
the full decode so an operator can inspect it live, but `redux sdr ingest` never
writes message text to the store.

## Ingest

```
redux sdr ingest <file> --pocsag --lat <y> --lon <x>   # multimon-ng POCSAG text
redux sdr ingest <file> --acars  --lat <y> --lon <x>   # acarsdec JSON
redux sdr ingest <file> --rtl433 --lat <y> --lon <x>   # ISM (default)
redux sdr ingest <file> --adsb                         # ADS-B (own position)
```

POCSAG capcodes and ACARS tails land in the **same Field Dex** as everything
else, tagged with the receiver's position (they carry none of their own).

## Spectrum sweep — listen to the bands

```
$ rtl_power -f 430M:440M:25k -i 1 -1 sweep.csv
$ redux sdr spectrum sweep.csv
{ "bins": 400, "noise_floor_db": -50.0, "span_mhz": [430.0, 440.0], "peaks": [...] }

active signals (strongest first):
  433.1000 MHz @ -28.0 dB (+22.0 above floor)
  ...
```

`parse_rtl_power()` turns the CSV firehose into a **noise-floor estimate**
(median of all bins, robust to strong signals) and the **active peaks** — the
frequencies transmitting right now, adjacent bins merged into one signal and
ranked by strength. Real data only: every number comes straight from the
measured dB bins; a quiet band reports no peaks rather than inventing one.

## How it's wired

- `redux/sdr/decoders.py` — POCSAG (`parse_pocsag_line`, `pocsag_to_sighting`,
  `ingest_pocsag`) and ACARS (`acars_to_sighting`, `ingest_acars`).
- `redux/sdr/spectrum.py` — `parse_rtl_power` → `SpectrumSweep` (bins,
  `noise_floor_db`, ranked `peaks`).
- `redux sdr ingest --pocsag/--acars` and `redux sdr spectrum` CLI.

Readers are injected (recorded samples in tests, a live subprocess on
hardware), so all of it is tested with no radio.
