# Sub-GHz signal library (`redux subghz`)

The Flipper-style toy for the junkyard haul: old keyfobs, garage remotes, and
cheap ISM gadgets that live **below 1 GHz** (300–928 MHz). Capture them, decode
what's decodable, catalog them, and — in the cage — replay them.

## Two halves, honest about the hardware

- **Capture / decode / catalog** — RX only, **works with a plain RTL-SDR today**.
  `rtl_433` decodes hundreds of these; raw IQ (`rtl_sdr`) grabs the rest.
- **Replay** — *transmitting* the signal back. An RTL-SDR **cannot transmit**,
  so replay needs a TX backend: **hackrf** (`hackrf_transfer`), **rpitx** (Pi
  GPIO as a transmitter — cage only), or **cc1101** (`rfcat`). Replay is a
  **POWER-tier** capability (see `docs/POWER_TIER.md`): hidden in FIELD, LAB-only,
  and a per-action `--confirm` every time. No TX backend present → replay is
  refused with a reason; capture/catalog still work.

## Honest limit: rolling codes

Modern car fobs and many garage remotes use **rolling codes** — replaying a
captured press does nothing (that's the security). The library detects likely
rolling-code protocols (KeeLoq, HCS*, …) and **flags them**, and replay refuses
them with a plain explanation rather than pretending.

## CLI

```
redux subghz capture 433.92M --seconds 10        # print the RX capture command
redux subghz capture 315M --raw --out cap.cu8    # raw IQ grab (for replay later)
redux subghz add garage-old 390M --protocol Generic --capture cap.cu8
redux subghz list                                # KeeLoq etc. shown [ROLLING]
redux subghz replay garage-old --confirm         # LAB + confirm + TX backend
```

```
$ redux subghz replay garage-old --scope-file lab.json
confirm required: 'Sub-GHz replay' — … Re-run with --confirm to proceed in LAB.
$ redux subghz replay carfob --confirm --scope-file lab.json
refused: 'carfob' looks like a ROLLING code — replay won't work (the remote's security, not a bug)
```

## Frequency cheat-sheet (sub-GHz)

| band | what lives there |
|---|---|
| 300–400 MHz | garage door openers, some keyfobs (US) |
| 315 MHz | TPMS (North America), keyfobs |
| 433.92 MHz | ISM: keyfobs, TPMS (EU), weather, remotes |
| 868 MHz | ISM (EU), smart-home |
| 915 MHz | ISM (US), LoRa, meters |

## Wiring

`redux/sdr/subghz.py` — `SubGhzLibrary` (JSON-backed catalog), `capture_command`
(RX), `looks_rolling`, `detect_tx_backend`, `replay_plan` (routes through
`power_tier.authorize_power("subghz.replay", …)`). `redux subghz` CLI. Depends on
the power-tier gate.
