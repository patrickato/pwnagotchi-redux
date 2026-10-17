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

## Independent integration stage — 2026-10-17

Implemented source-only, with synthetic tests in
`tests/test_connectivity_integration.py`:

- `redux/radio/split_integration.py` binds read-only iw capability output,
  *per-interface* measured bus metadata and independent RadioEvidence to the
  split planner. `bind_probe_observations` preserves extra VIFs instead of
  collapsing them behind the legacy preferred monitor name. All interfaces
  must have fresh, matching PHY/mode and positive ifindex; unknown transport,
  duplicate PHY/index, missing management or stale evidence blocks the plan.
  Explicit owner roles distinguish NM management from exclusive capture;
  a generic owner_verified flag cannot qualify an NM-owned USB adapter.
  Injection guesses are discarded. The caller must independently measure
  bus attachment and bound snapshot age; no periodic live collector is installed.
- Capture leases pin iface/PHY/ifindex and exclusive measured ownership;
  `lease_present` rejects removal, name reuse, shared capture PHY, ownership
  loss, non-native framing or lost management verification. It never chooses
  a fallback. Rebuilding a lease requires requalification, including fresh
  Ethernet proof rather than trusting a previous SplitPlan label.
- Capability probe now resolves the sysfs device symlink before walking USB
  parents. Nonfinite, negative and pre-USB2 speeds no longer claim USB2/3.
  Driver-name inference of onboard status in the older probe is still a
  heuristic; the measured bridge uses explicit transport evidence instead.
- Split inventory validation rejects truthy string monitor/onboard claims
  and malformed interface/PHY identities. RadioManager marks interrupted
  transitions out of sync even for a session signal/KeyboardInterrupt.
  Return-code success still needs independent measured mode/ownership
  readback before a future live split executor may claim applied roles.
- PhoneSession now bounds a trusted reconnect attempt (default 30 seconds),
  rejects a late success and yields disconnect before backoff on timeout.
  Disabling during CONNECTING explicitly cancels it. Pairing never opens
  as a reconnect fallback. The eventual backend must poll tick and verify
  stop intents; pure policy state is not observed controller state.
- `redux/connectivity/bluez_pan.py` prepares declarative Network1.Connect
  requests only for an explicitly enabled, due reconnect with paired,
  trusted, separately authenticated bond and advertised NAP evidence.
  Network1 Connected/Interface/UUID must match the returned bnep netdev and
  the management IP route must be independently verified. Pairing property
  plans set finite PairableTimeout/DiscoverableTimeout before opening both
  flags and close both flags on window exit. No bus executor, pairing agent,
  trust setter, DHCP/NM/firewall or Bluetooth service is installed or started.

BlueZ [Network1](https://bluez.readthedocs.io/en/latest/network-api/) provides
PAN profile connectivity, not an authenticated Redux management session.
Keep a long-lived bus owner: its disappearance releases the client network;
call Disconnect on attempt timeout. A future adapter must pin private peer
identity and D-Bus service owner/generation, reject stale replies, re-read
trust/security before each reconnect, and verify encryption/application
credentials separately. Device1 Trusted alone is not pairing authentication.
The [Adapter1 API](https://bluez.readthedocs.io/en/latest/adapter-api/) exposes
Pairable separately from Discoverable, so stopping discoverability alone
would not close incoming pairing. Requests/peer paths are private and must
never be serialized into Doctor/public reports.

### Bettercap review and integration boundary

Existing LiveRuntime stops/reaps its writer before queuing a nonempty closed
PCAP; termination_pending prevents another engine or handoff while the old
child survives. Existing tests cover terminate/kill/wait failures and durable
handoff. Its generic choose_safe_radio retry path can select another eligible
radio, including disconnected onboard Wi-Fi, and tracks interface name rather
than a split lease. **The new lease planner is not yet wired into this live
path.** Use no auto-deployment or claim that generic runtime currently enforces
split reservations. A future opt-in executor must reserve onboard Wi-Fi,
verified Ethernet and any PAN management netdev, pin a qualified USB lease,
stop/reap/close/handoff before reacquiring, and block on USB removal until
fresh identity/ownership/management measurements qualify a new lease.
A preferred interface limits name fallback but does not solve ifindex reuse.

### Actual read-only Pi inventory

Strict verified ED25519 SSH, no mutations: only wlan0/phy0/ifindex3,
brcmfmac, SDIO/platform attachment, advertised monitor and 2.4/5 GHz,
no measured USB generation. No external Wi-Fi radio was enumerated.
This inventory does not measure reception, injection, split ownership,
Bluetooth pairing/PAN, or capture handoff. No radio was created or retuned.

### Remaining acceptance and preserved native-VIF work

USB bus/power/kernel health, actual monitor ownership/link type/decoded RF,
management continuity, hot-unplug/replug/ifindex reuse, closed-file handoff
and conversion need their own explicitly approved external-radio field run.
Bluetooth needs a reviewed bus executor/agent, outcome/timeouts/identity
handling, PAN IP/application authentication and a separate approved field
procedure before any boot service. Source and synthetic tests do not pass
these hardware gates.

NM 1.42.4's deliberate monitor-device exclusion and unsupported
ID_NET_MANAGED_BY finding remain separate and unchanged. The prepared,
unexecuted [DOWN-only ownership observation](DOWN_ONLY_VIF_OBSERVATION.md)
now lists exact commands, bounded measurements and gated rollback. It still
requires a dedicated reviewed offline-tested execution mode before any new
hardware approval request. No existing VIF authorization remains active.

Offline qualification for this stage: **1,827 passed, 2 skipped** in the
full suite, plus **517 image-contract tests passed**. Included existing
split/Ethernet/phone/manager/Bettercap lifecycle regressions and new measured
bridge, lease reuse/removal, explicit owner roles, PAN outcome/privacy,
finite pairing properties, timeout/cancel and USB sysfs tests. No source
change in this stage was installed on the Pi.
