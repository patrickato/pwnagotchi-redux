# NG-inspired connectivity and split-radio integration — 2026-10-17

This document records **implemented source code**, **acceptance gates**,
and **not-yet-implemented device wiring**. It does not imply physical Pi
changes, completed RF reception, or a deployable Bluetooth service.

## Implemented now

### USB capture + onboard management qualification

Source: `redux/radio/dual_radio.py`, tested in
`tests/test_dual_radio.py`. Public API:
`from redux.radio import RadioEvidence, plan_dual_radio`.

- Consumes existing capability-probe `Radio` records and explicit,
  independently observed `RadioEvidence`.
- Reserves the positively identified onboard Wi-Fi PHY from capture.
  Management may use either an independently verified connected/NM-owned
  onboard uplink **or a separately verified Ethernet return route**.
  Neither path is presumed from interface names alone.
- Only considers externally attached, monitor-capable radios on a
  **different, positively identified PHY** as capture candidates.
- A mere `iw type monitor` or ARPHRD 803 label does **not** prove
  reception. `receive_verified` requires monitor mode, positive
  ownership, native packet link type, and **strictly positive received,
  decoded 802.11 frame count**.
- No external radio, missing management-path evidence, duplicate PHY/iface,
  disconnected uplink without verified Ethernet, or invalid observation
  fails closed. Removing
  the USB adapter does not silently promote/retune the management
  radio. Planning never issues `iw`, `ip`, NM, RF or service commands.
- Every returned plan has `activation_authorized=False`: operator
  approval of future physical tests and the live runner's specific
  hardware gates are separate concerns.

Illustrative **offline-only** usage (not measured actual Pi readiness):

```python
from redux.radio import Radio, RadioEvidence, plan_dual_radio

inventory = [
    Radio("wlan0", phy="phy0", monitor=True, onboard=True),
    Radio("wlan1", phy="phy1", monitor=True, driver="mt76x2u", usb_gen=3),
]
evidence = {
    "wlan0": RadioEvidence("phy0", mode="managed",
                           owner_verified=True, management_connected=True),
    "wlan1": RadioEvidence("phy1", mode="monitor", arphrd=None,
                           decoded_frames=None, owner_verified=False),
}
plan = plan_dual_radio(inventory, evidence)
assert plan.state == "candidate" and not plan.activation_authorized
```

The optional `ethernet_management_verified=True` argument is only
for callers that have **independently proven** the Ethernet connection
and return path (e.g., the Pi's already-verified strict Ethernet SSH
preflight). A missing, untrusted or string-valued proof stays blocked.

This is a conservative preflight component, **not yet wired to Bettercap,
udev hotplug or a systemd service**. External capture remains untested
on this image; no field claim is implied.

### Bluetooth phone-management policy engine

Source: `redux/connectivity/phone.py`, tested in
`tests/test_phone_session.py`.

- `PhoneSession` starts **disabled**; no service or pairing is enabled
  just because the Python module is installed.
- A new pairing window is opened only by an explicitly owner-authorized
  call, defaults to 90 seconds, and expires even if a pairing event
  arrives late.
- Both authenticated pairing and trusted/bonded peer evidence are
  required before a phone link is admitted. No default passcodes,
  peer IDs, pairing secrets or address strings live in this layer.
- Disconnect schedules a capped exponential reconnect only for an
  already trusted peer; it **never reopens discoverability**.
- Status contains only allowlisted state names and bounded durations,
  no device identifiers. It is driven by an injected monotonic clock.
- All operations yield declarative `PhoneIntent` events; they do
  not change BlueZ, Bluetooth controller state, NetworkManager, firewall,
  or any physical Pi property.

This implements the platform-neutral **policy and recovery logic**, not
the actual Bluetooth PAN/BlueZ pairing agent or data transport.

## Next source-only engineering gates

1. Bind real, **read-only observed** USB iface/PHY/kernel-link facts to
   `RadioEvidence`, independently check capture ownership and decoded
   reception; keep management route continuously verified. Preserve
   existing strict Ethernet SSH safety when a live field test is approved.
2. Review how `LiveRuntime.choose_safe_radio` and `RadioManager`
   consume split decisions. A change that drives hardware must not
   assume `Radio.inject` from driver-name heuristics means injection
   or actual receive works. Make radio role transitions transactional
   and fail closed if an interface mode command fails. The current
   `RadioManager` now rejects OS command failures/timeouts, prevents
   publishing an unapplied role assignment and marks partially applied
   transitions out of sync until a fresh observed reprobe. This is
   source-tested but not yet deployed on the Pi.
3. Add a narrowly scoped BlueZ adapter whose own pairable/discoverable
   settings have an explicit expiring owner-triggered gate. Use a
   time-bounded pairing agent, authenticated bond identity, encrypted
   management traffic, reconnect timeouts and denial-of-service limits.
   Avoid broadcasting a persistent default PIN or exposing the control
   API to arbitrary phones.
4. Define PAN/NM interface reservations (PAN is management, never a
   capture radio), service lifecycle ownership, local authentication,
   and a read-only status/Doctor view of current connection health.
5. Add adapters that **verify outcomes**, not merely issue intents.
   Unit and synthetic integration tests first; deployment and user
   acceptance require separate hardware authorization.

## Next physical gates (not approved here)

- A USB Wi-Fi adapter is connected and its **actual** driver, PHY,
  supported bands, monitor support, ARPHRD, and ownership are measured.
  Do not assume a particular Alfa chipset/model. First verify
  USB power and kernel logs read-only.
- Proposed one-attempt external-radio test must separately spell out
  physical changes, allowed commands, rollback, tested interface,
  time limits and strict Ethernet escape; no default auto-switch.
- Receive **real decoded 802.11 frames**, then nonempty closed PCAP,
  handoff, verified ingest/conversion, and a stable management
  connection. Empty counters or a header type do not count.
- Bluetooth pairing and PAN connectivity need their own approved
  physical test and rollback before enabling any boot service.

The currently pending **internal Nexmon VIF** investigation stays
independent. The last verified trial created radiotap ARPHRD803,
but temporary NM ownership was unknown and firmware2/packets were not
measured. NetworkManager 1.42.4 intentionally ignores newly discovered
monitor interfaces, per Codex's pinned-source audit. Do not work around
its ownership check by interpreting absence as permission to activate.

## Third-party attribution

The architectural inspiration came from publicly announced
Pwnagotchi-NG development and patterns visible in
[aluminum-ice/pwnagotchi](https://github.com/aluminum-ice/pwnagotchi).
Both of the newly committed Redux modules above were written for
Redux rather than copying third-party implementation. If specific
GPL-licensed upstream code is later copied, retain notices and
attribution and review dependency licenses.
