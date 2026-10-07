# RSSI-gradient fox-hunt (`redux/hunt/`)

## Why

Physically walk onto a scoped target using the RSSI the sighting store already
logs — "warmer / colder," a coarse proximity band, and (with GPS fixes) a position
estimate + a bearing. No FTM, no extra hardware. (We looked at 802.11mc FTM
ranging and it isn't Pi-ready — this is the realistic hunt.)

## What's built (sandbox-verified)

- `FoxHunt(target)` with `observe(HuntObservation(rssi, ts, lat?, lon?)) -> HuntState`.
- **Trend** (warmer/colder/steady/unknown) — a responsive recent-vs-prior slope, so
  a retreat after an approach flips to "colder" promptly instead of being masked by
  the earlier climb.
- **Band** — a coarse proximity label (on top of it / very close / close / nearby /
  far), explicitly labelled environment-dependent.
- **Estimate + bearing** — reuses the geo weighted-centroid (`estimate_from_coords`,
  with its own error radius) once there are ≥2 GPS-tagged samples, plus a compass
  bearing from the latest position to the estimate.
- CLI: `redux hunt demo` (simulated approach then retreat).

## The honesty line

- **Trend is relative and robust** — it's a direction, not a distance.
- **Distance is a band, never fake meters.** RSSI→range is wrecked by walls,
  bodies, and multipath, so the hunt gives a band with the caveat stated.
- **The estimate/bearing only appear with enough fixes** — before that they're
  None, not a guess, and the estimate carries the centroid's error radius.

## Needs-hardware

Live RSSI from the radio and a real GPS feed; the trend/band/estimate math is all
sandbox-verified against synthetic gradients.
