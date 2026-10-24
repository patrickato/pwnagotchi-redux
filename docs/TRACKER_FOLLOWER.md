# Unwanted-tracker detection: "is it following me?" (`redux tracker`)

The BLE tracker detector (`redux/detect/ble_tracker.py`) already flags *what* a
device is — an AirTag/Find My advertiser, a skimmer module. This answers the
scarier question the Apple/Google unwanted-tracking alerts answer: **is this
thing moving with me?**

## The heuristic

A device is a potential **follower** only when the SAME identifier is seen
**persistently over time** AND **across multiple distinct locations spanning
real distance** — i.e. you moved and it stayed with you. Explicitly *not*
flagged:

- a device seen once (you just passed it),
- a device seen only in **one** place (a neighbor's tag, a fixed beacon) — even
  if seen for hours,
- a device seen at two distant points but seconds apart (you drove past it).

GPS jitter at a single spot is collapsed so it can't masquerade as "many
locations." Every verdict is glass-box — how long, how many places, how far:

```
$ redux tracker
⚠ 1 device(s) may be following you:
⚠ ff:ee:dd:cc:bb:aa  FOLLOWING: seen 3x over 20 min across 3 places spanning 667 m — it moved with you
```

## Why a separate observation log

The sighting store keeps **one row per device** (`UNIQUE(kind, mac)`), so it
shows a device's latest position, not its movement. Follower analysis needs the
**time series**, so the live runtime appends BLE observations — each tagged with
the receiver's current GPS, like an ISM sighting — to an append-only JSONL
(`/captures/live/observations.jsonl`), bounded and pruned. `redux tracker` reads
that log.

```
redux tracker [--log <path>] [--min-minutes 10] [--min-span-m 100] [--all]
```

`--all` shows every analyzed device with its reason (why it is or isn't a
follower), not just the flagged ones.

## Honest by construction

- A device with **no location history** is reported as "can't tell if it moved
  with you" — never guessed to be following.
- The confidence score is simply how many of the four conditions (duration,
  distinct locations, travel span, hit count) are met — no black box.
- Real data only: observations come from actual sightings tagged with actual
  GPS fixes.

## Wiring

`redux/geo/follower.py` — `follower_analysis`/`analyze_mac`/`followers` (pure),
`ObservationLog` (the time-series recorder + bus subscriber). `live_runtime`
subscribes it passively; `redux tracker` CLI reads it.
