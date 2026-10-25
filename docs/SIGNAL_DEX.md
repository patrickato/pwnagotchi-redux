# Signal Dex — one catalog across every radio/medium (`redux signals`)

WiFi / BLE / ISM / ADS-B / pager / ACARS live in the sighting store; captured
**sub-GHz** signals live in the SubGhz library; learned **IR** codes live in the
IR library. The Signal Dex folds them all into one browsable view, so
"everything I've seen or captured, across every medium" is a single pane instead
of six — handy out in the lab and great while learning.

```
$ redux signals
Signal Dex: 5 signal(s) across 4 medium(s)
  by medium: wifi=1, adsb=1, subghz=2, ir=1
  [wifi   ] b8:27:eb:11:22:33    pi-ap        legendary, RaspberryPi
  [adsb   ] a1b2c3               UAL123       legendary
  [subghz ] sg002                carfob       433.92 MHz, KeeLoq [rolling — won't replay]
  [subghz ] sg001                garage-old   390 MHz, Generic
  [ir     ] Living room TV power ...           NEC
```

`--domain subghz` filters to one medium; `--limit N` caps output. The WiFi/BLE
rows carry the Field Dex's vendor + randomized-MAC enrichment automatically.

Read-side only, no fabrication: it reads the real stores/libraries you point it
at (all injectable), normalizes each into a common `SignalEntry`, and never
invents a row — omit a source and it's simply absent. `redux/dex/signal_dex.py`
— `build_signal_dex(sighting_store=, subghz_lib=, ir_lib=)` → `SignalDexView`.
