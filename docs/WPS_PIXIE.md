# WPS / Pixie-Dust (`redux wps`)

WPS is the classic fast win: a vulnerable AP's WPS exchange can be turned into
the WPA PSK in **seconds** offline ("pixie-dust" via pixiewps), or recovered by
PIN brute (reaver/bully). This wires it in the project's way — scope-aimed,
posture-gated, glass-box — mirroring the EAP plan pattern.

## Passive vs firing

- **Finding** WPS-enabled APs is passive: `redux wps scan <wash-output>` parses
  a `wash` capture into targets (with lock state). Always allowed.
- **Attacking** one is firing — it transmits at a specific AP — so it's gated:
  - **FIELD**: the BSSID must be in scope **and** you pass `--authorized`.
  - **LAB**: approved on sight (turnkey), because the scope's lab mode
    authorizes your own gear.

## CLI

```
redux wps scan wash.txt                              # list WPS APs (passive)
redux wps plan --bssid <b> --channel <n> [--tool reaver|bully] [--no-pixie]
redux wps run  --bssid <b> --channel <n> [--authorized]   # gated; LAB = turnkey
```

```
$ redux wps plan --bssid aa:bb:cc:dd:ee:01 --channel 1 --scope-file lab.json
LAB: WPS attack on aa:bb:cc:dd:ee:01 — approved on sight; pixie-dust (offline) — seconds on a vulnerable AP

command:
  reaver -i wlan1mon -b aa:bb:cc:dd:ee:01 -vv -c 1 -K 1
```

`plan` prints the exact `reaver`/`bully` argv and, honestly, whether the tool is
even installed. `-K 1` (reaver) / `-d` (bully) is the pixie-dust path; drop it
with `--no-pixie` for an online PIN brute. A **WPS-locked** AP is flagged — PIN
brute will be rate-limited there (pixie may still work from a single exchange).

## Honest + fail-closed

- A refused plan carries its reason and emits **no** command.
- `run` surfaces a recovered **WPA PSK** (preferred) or WPS PIN from the tool's
  output, and reports `completed`/`error` otherwise — never a fabricated result.
- The runner is injected, so the whole flow is tested with no radio and no tools
  installed.

## Wiring

`redux/wps.py` — `parse_wash`, `WpsTarget`, `authorize_wps` (the scope/posture
gate), `plan` (reaver/bully pixie-dust argv), `run_plan` (injected runner,
PSK/PIN extraction). `redux wps scan|plan|run` CLI.
