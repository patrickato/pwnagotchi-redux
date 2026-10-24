# Waterfall: making the spectrum visible (`redux sdr waterfall`)

`redux sdr spectrum` reduces a sweep to peaks + a noise floor — great for "what's
on the air," but it throws away the time dimension. A **waterfall** keeps time:
each sweep is a row, stacked newest-last, so you can *see* a signal come and go
across a band.

```
$ rtl_power -f 88M:108M:500k -i 1 -e 15s fm.csv
$ redux sdr waterfall fm.csv --width 40 --out fm.pgm
88.000 MHz                    107.500 MHz
 .. . ..@ . .  . ..@ ..      . %    ...
 ... .. %....  .  .%.  ... .  .@.  .....
...  (time flows downward; the @ / % columns are persistent stations)
15 sweep(s) × 40 bins, -63..-11 dB
wrote grayscale image: fm.pgm (PGM)
```

Two dependency-free renderings:

- **ASCII** — shows up anywhere text does: a terminal, the TFT's text pages, or a
  web `<pre>`. A faint→strong ramp (` .:-=+*#%@`) maps signal intensity; a narrow
  strong signal survives downsampling because columns take the **max** in each
  bucket, not the mean.
- **PGM** (`--out`) — a standard grayscale image (rows=time, cols=frequency),
  written in pure Python, viewable in any image viewer or a web `<img>`.

## Where this sits (honest)

This is **tier 1** of the waterfall: it turns the sweep data we already have into
a real picture, today, and is fully testable with no radio. What it is **not**
yet: a real-time stream pushed live to web + phone + TFT simultaneously — that
needs a continuous `rtl_power` producer and an SSE/websocket transport, which are
later tiers. The model here (`Waterfall`) is the shared foundation those surfaces
will render.

## Wiring

`redux/sdr/waterfall.py` — `parse_rtl_power_rows` (groups CSV by sweep timestamp,
keeping the time axis), `Waterfall.from_rtl_power`, `to_ascii`, `to_pgm`.
`redux sdr waterfall` CLI. Pairs with the spectrum/peaks analysis.
