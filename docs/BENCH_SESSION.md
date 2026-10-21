# The bench session — all hardware validation in one sitting

This is the page to read when you're home with the Pi and the gear. Everything
that needs real hardware is batched into one resumable pass driven by
`redux benchpass`, so you walk every gate once, in order, and get a single
consolidated result. The full per-gate detail lives in
[`HARDWARE_VALIDATION.md`](HARDWARE_VALIDATION.md); this page is the flow.

The pass judgment is always yours — hardware gates need a human to attest.
`benchpass` sequences the gates, runs/shows the command for each, records your
`pass`/`fail`/`skip` to a resumable ledger, and prints one report. It never
transmits and never fabricates a result.

## 1. Before you travel home — plan it (no hardware)

```sh
redux benchpass prep
```
Prints the **gear to stage**, every gate in order, and the exact command for
each. Stage the gear in that list (your own AP + client, a monitor/injection
adapter, optional TFT/GPS/LoRa nodes, etc.).

Dry-run the headline flow anywhere to see it reason, with no hardware:
```sh
redux demo --intent hunt        # sample inventory; prints the arrange→capture plan + why
```

## 2. At the bench — arm your scope, then preflight

Authorize **only your own gear** (this is what every firing gate aims at):
```sh
redux scope arm-lab --auto      # or: redux scope add <your BSSID/SSID>
```

Check prerequisites before starting, so you don't fail partway:
```sh
redux benchpass preflight       # green/red board: radios, engine, /captures, battery
```
Resolve any `MISS` rows, then go.

## 3. Run the pass (resumable)

```sh
redux benchpass run             # walks every gate; record pass/fail/skip each
```
- It shows each gate's command and pass criterion; you run it, judge it, type the result.
- `quit` any time — progress is saved. Continue with `redux benchpass run --resume`.
- Focus on a subset with `--only 1.6 2b.6`.
- The headline (gate 1.6) is just: `redux demo --live --intent hunt --offense` on your own armed AP — flash → boot → plug → auto-arrange → capture, hands-off. Record a short clip.

## 4. Read the result, record it

```sh
redux benchpass report          # consolidated pass/fail/skip across all gates
```
Then, per [`HARDWARE_VALIDATION.md` §3](HARDWARE_VALIDATION.md): append a dated
block to `IMAGE_BUILD.md`'s hardware-gate record and flip the matching `TASKS.md`
rows — `done` only when a gate passes on **both** Pi 4 and Pi 5; otherwise leave
`done (sandbox)` with a note on exactly what failed and on which board.

## Scope reminder

Everything here runs on **your own** gear in **your own** lab. Any firing step is
gated by the central Scope armed to your own targets — never a third party. If a
step would transmit at anything outside your own allowlisted kit, stop.
