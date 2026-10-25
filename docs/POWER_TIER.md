# Power-tier gating — the hard gate for the strongest tools (`redux power`)

Scope decides WHERE the device may aim; mode (LAB/FIELD) decides the posture.
This adds the third axis you asked for: a **risk tier**, stricter than ordinary
gating, for the genuinely powerful, easily-misused capabilities.

## Three tiers

- **NORMAL** — ordinary capabilities, gated the usual way (scope/mode).
- **POWER** — the sensitive power-user tools (RF transmit/replay, sub-GHz
  replay, IR blast, BadUSB/HID, BT HID). These:
  1. **do not appear at all in FIELD** — they're not options, not merely
     refused;
  2. are available **only in LAB**;
  3. even in LAB require an explicit **per-action confirmation** — you opt in
     ("are you sure? OK") *every time*, via `--confirm` / a dialog. Toggling LAB
     is not enough.
- **FORBIDDEN** — things that can't be aimed and hit bystanders (**RF jamming**,
  **indiscriminate beacon/BLE spam**). Never available, in any mode, confirm or
  not. Detection-only, per the project contract and the law.

## See it

```
$ redux power --mode field list      # powerful tools are HIDDEN here
capabilities available in FIELD mode:
  (none)
not available here:
  [POWER — LAB only]  sdr.replay   re-transmits a captured signal …
  [FORBIDDEN (never)] rf.jam       indiscriminate denial of a band …

$ redux power --mode lab list        # they appear in LAB, each "needs confirm"
  [power] sdr.replay   RF capture replay (needs confirm)
  ...

$ redux power --mode lab check sdr.replay
REFUSE: confirm required: 'RF capture replay' — … Re-run with --confirm to proceed.
$ redux power --mode lab check sdr.replay --confirm
ALLOW: authorized: 'RF capture replay' in LAB with explicit confirmation
```

## For feature authors

This is the single source of truth. A new powerful feature:

1. `register(PowerCapability("subghz.replay", "Sub-GHz replay", Tier.POWER, "<risk>"))`
   in `power_tier.py` (several are pre-seeded).
2. routes its fire step through
   `authorize_power(cap_id, lab_mode=…, confirmed=…)` and refuses on `(False, …)`.
3. a UI lists only `visible_in(mode)` — so POWER tools never render in FIELD.

That guarantees the gate is consistent everywhere: nothing powerful can ship a
path that bypasses the FIELD-hide + LAB + confirm rule, and nothing FORBIDDEN can
be exposed at all.

## Why this shape

In the field/public you can't control what's in range, so the powerful tools
have no safe use there — hence hidden, not just gated. In a shielded lab on your
own gear you can authorize anything, so they unlock — but the confirmation keeps
a slip of the finger from firing a transmit/inject by accident. Forbidden stays
forbidden because it harms bystanders regardless of intent.
