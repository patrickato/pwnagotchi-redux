# MAC vendor enrichment & device classification (`redux oui`)

Every MAC the device sees carries a 24-bit vendor prefix (OUI). Turning that
into a vendor name — and flagging the MACs that are **randomized** (phones
rotate a locally-administered address for privacy) — makes every survey, Dex,
and sighting far more legible: *"a Raspberry Pi and three Espressif IoT
boards"* beats eight raw hex strings.

The OUI helpers (`redux/geo/oui.py`) used to be an 8-entry stub that nothing
called. This wires them up for real.

## What you get

- **A curated built-in vendor table** (~100 prefixes) covering what a Wi-Fi/BLE
  recon device actually sees: Apple, Samsung, Google, Amazon, Espressif
  (ESP32/8266), Raspberry Pi, Intel/Realtek/Broadcom/Qualcomm chipsets,
  TP-Link, Netgear, Cisco/Meraki, Ubiquiti, Aruba, Xiaomi/Huawei, Tuya/Shelly,
  Sonos/Roku/Nest/Ring. Deliberately **not** the full 35k-row IEEE registry —
  that stays out of the repo to avoid bloat.
- **Randomized-MAC detection** from the locally-administered bit (`0x02` of the
  first octet). This is a *hard fact*, not a guess: a burned-in manufacturer MAC
  has it clear, a privacy/randomized MAC has it set.
- **A coarse device-class guess** from the vendor (`sbc`, `iot`, `mobile`,
  `smart-home`, `network-infra`, `nic/chipset`, …) — surfaced explicitly as a
  *guess from the vendor*, never a claim about the real device.
- **Full-DB import**: an operator can import a complete IEEE `oui.txt`, a
  Wireshark `manuf`, or a normalized TSV once; it's merged on top of the
  built-in table from then on.

## CLI

```
redux oui lookup <mac>      # vendor name, or the bare MAC if unknown
redux oui classify <mac>    # vendor + device-class guess + randomized flag (JSON)
redux oui import <file>     # merge a full IEEE/manuf/TSV DB (default -> /captures/config/oui.tsv)
redux oui stats             # counts for the active table
```

```
$ redux oui classify da:a1:19:44:55:66
{ "mac": "da:a1:19:44:55:66", "vendor": null, "device_class": null,
  "randomized": true,
  "reason": "randomized/private MAC (locally-administered, no vendor) — likely a phone/privacy address" }
```

## Wired into the Dex

`redux dex` now enriches every entry with `vendor`, `device_class`, and
`randomized`, and the summary gains `by_vendor` (top 10), `identified_vendors`,
and `randomized` counts:

```
Field Dex: 42 known · 8 located · 3 departed · vendors 11
  identified: 27 (Apple=9, Espressif=6, RaspberryPi=2, …)
  randomized MACs (privacy/phone): 11
```

## Honest by construction

- A vendor we don't know returns **nothing** — never a fabricated name.
- `device_class` says *"(guess from vendor)"* in its own reason.
- `randomized` is derived from the address bits, not inferred from behavior.
- The full-DB import fails closed: an unparseable source writes nothing and
  says why.
