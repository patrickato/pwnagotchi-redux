# Guided SDR: pick what to listen to, Augur tunes it (`redux sdr presets` / `tune`)

The decoders read the *output* of an SDR tool. This is the other half a new user
needs: the **band plan** that knows, for a plain-language choice, the exact tool
+ frequency + sample rate + arguments to run. You pick "Pagers"; Augur produces
the tuned `rtl_fm | multimon-ng` pipeline. No frequency math, no memorized flags.

## The flow

```
redux sdr presets                 # 1. see your choices, in plain language
redux sdr tune <preset>           # 2. get the exact tuned command (Augur sets the frequency)
#    run it (the live layer does this for you on-device)
redux sdr ingest <file> --<fmt>   # 3. the matching decoder reads its output
```

```
$ redux sdr presets
  ism       Home/ISM sensors @ 433.92M        weather stations, thermometers, meters, key fobs
  tpms      Tire sensors (TPMS) @ 315M        the pressure sensors inside passing cars' tires
  aircraft  Aircraft (ADS-B) @ 1090M          airliners broadcasting their position
  pagers    Pagers (POCSAG) @ 929.6M          POCSAG transmissions (capcodes only, no messages)
  acars     Aircraft messaging (ACARS) @ 131.55M
  scan      Scan a band                       find active signals when you don't know where to look

$ redux sdr tune pagers
run this:
  rtl_fm -d 0 -f 929.6M -s 22050 -g 40 - | multimon-ng -t raw -a POCSAG512 -a POCSAG1200 -a POCSAG2400 -f alpha -
then: redux sdr ingest <file> --pocsag
```

## Honest about the hard part of RF

Frequencies are **region- and deployment-specific** — pager and ACARS channels
especially. Every preset whose frequency depends on where you are carries a
`region_note` saying so and how to override it (`--freq 433.92M`), rather than
pretending one number works everywhere. For TPMS that's 315 MHz in North America
vs 433.92 MHz elsewhere; for pagers it varies by country.

If you don't know what to listen to, start with **`scan`** — it sweeps a band
(`rtl_power`) and `redux sdr spectrum` shows the active peaks, so you can see
where to point a decoder.

## For the UI (checkboxes later)

`list_presets()` and `SdrPreset.command(...)` are the data + brain a TFT/web
checkbox picker will read directly: show the labels and `what`, let the user
pick and optionally override `--freq`/`--device`, and run `command().shell()`.
The guided logic lives here once, so every surface stays consistent.

## Wiring

`redux/sdr/presets.py` — `SdrPreset` (catalog entry with help + region note),
`PRESETS`, `find_preset`, `list_presets`, and `command()` (the tuned pipeline).
`redux sdr presets|tune` CLI. Pairs with `redux/sdr/decoders.py` +
`spectrum.py`, which parse the output.
