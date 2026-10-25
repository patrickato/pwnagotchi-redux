# Infrared: TV-B-Gone + loadable IR library (`redux ir`)

The Pi can be a universal remote: an **IR LED** on a GPIO pin transmits, an **IR
receiver** (TSOP38238) learns — both cents of e-waste (any dead remote donates
an emitter). This module is the catalog + the gated command plans; the blink
happens through LIRC (`irsend`) / pigpio on the device.

- **TV-B-Gone** — `redux ir blast` sends a sequence of power codes so an
  un-remoted TV turns off/on. A built-in starter set of common-brand power codes
  ships; import the full database as Flipper `.ir`.
- **Loadable library** — `redux ir import <file.ir>` parses **Flipper `.ir`**
  (both parsed and raw blocks) so you can drive your *specific* gear;
  `redux ir replay <name>` fires one code.

IR transmit is **POWER-tier** (`ir.blast` / `ir.replay`): hidden in FIELD,
LAB-only, per-action `--confirm` (it hits any IR device in line of sight).

    redux ir codes                    # built-in TV power codes
    redux ir import living_room.ir    # load Flipper codes
    redux ir list
    redux ir blast --confirm          # LAB only; blasts every power code
    redux ir replay "MyTV power" --confirm

Needs an IR LED (plus optional receiver) on GPIO and LIRC configured; `blast` /
`replay` print the exact `irsend` command. Code lives in `redux/ir.py` —
`IRCode`, `IRLibrary`, `parse_flipper_ir`, and `blast_plan` / `replay_plan`
(routed through the power-tier gate).
