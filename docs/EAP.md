# WPA-Enterprise EAP credential capture (`redux/eap/`)

## Why

The corporate red-team bread-and-butter pwnagotchi never had. Against a
WPA-Enterprise (802.1X) network, stand up a rogue enterprise AP that offers EAP
methods; a client that tries to authenticate hands you a MSCHAPv2
challenge/response (a crackable hash) or, on a GTC downgrade, a cleartext inner
credential. This is the eaphammer / hostapd-mana playbook — the single most
"real job" capability the offense suite was missing.

## Firing-capable → gated like the rest of offense

It transmits a rogue AP, so it refuses unless **all** of:

- the mimicked **SSID is in the central Scope** (Scope decides WHERE),
- **offense posture is active** (a detection-only persona refuses — no transmit),
- an explicit **`authorized=True`** confirms it's a test you're permitted to run.

`redux eap plan --ssid CorpWiFi --authorized` shows the gated plan (and refuses,
with the reason, when any gate fails).

## What's built (sandbox-verified)

- `MschapV2Credential` → `hashcat_5500()` (`user::::<response>:<challenge>`) and
  `john_netntlm()` — the enterprise analogue of AngryOxide's 22000 output: a
  capture turned straight into a crackable line.
- `GtcCredential` — a GTC-downgrade capture, already cleartext.
- `parse_hostapd_wpe(log)` → credentials (fenced needs-hardware *format*; the
  conversion doesn't depend on it — it takes fields directly).
- `EapHarvester` — honest tool-absence for the AP binary; `plan()` is fully gated.
- `authorize_eap(scope, ssid, authorized, active)` — the reusable gate.
- Augur `eap_plan()`; CLI `redux eap plan|parse`.

## Needs-hardware

The actual rogue-AP stand-up (hostapd-mana/eaphammer, a server cert, a radio) and
a real client authenticating. The plan builder, the gate, and the crackable-line
conversion are all sandbox-verified; the capture itself is a Pi + radio step.
