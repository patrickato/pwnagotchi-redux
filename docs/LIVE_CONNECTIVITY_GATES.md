# Live USB ownership and opt-in Bluetooth PAN implementation

**Source implementation; no hardware execution or new release image.**
The Pi's installed code, services, settings and original cards remain untouched.
This document supersedes earlier statements that these executor paths were only
plans. Their physical acceptance remains open.

## Live Bettercap USB ownership

`redux/radio/live_lease.py` is integrated into LiveRuntime startup, engine
readiness, every runtime tick, driver commands/events and teardown.
`BettercapDriver` supports a guard before and after transport operations and
pins interface selection when leased. Results received after lease failure
are discarded. Bettercap still must stop/reap before closed-file handoff;
termination_pending prevents releasing the lease or starting another child.

USB capture requires explicit `live.require_usb_lease=true`, an explicit
preferred interface, an absolute evidence path and a management return-path
peer. Connected-capture override is incompatible. Defaults remain false;
positively detected USB interfaces cannot fall through to legacy mode setters
when this feature is disabled. The onboard legacy path is otherwise unchanged.
No management interface is retuned or adopted as a capture fallback.

A root-owned, non-group/world-writable regular evidence receipt (no leaf
symlink, maximum 64 KiB) supplies **real collector observations**, not operator
authorization. FileLeaseEvidence accepts this schema:

```text
{
  "boot_id": <current /proc/sys/kernel/random/boot_id>,
  "sampled_monotonic": <same-Pi monotonic sample time>,
  "ethernet_verified": true,
  "radios": [{
    "iface": <observed netdev>, "phy": <observed PHY>, "ifindex": <observed index>,
    "monitor": <advertised capability boolean>, "bands": <advertised band list>,
    "mode": <measured kernel mode>, "arphrd": <measured link type>,
    "decoded_frames": <independently decoded receive count or null>,
    "owner_verified": <measured boolean>, "owner": <explicit owner role>,
    "transport": <measured usb or onboard>
  }]
}
```

Receipts must be atomically refreshed by a separately qualified local collector;
observations older than three seconds, future samples, old boots or clock
regression are rejected. A metadata file or driver allowlist cannot manufacture
receive qualification. There is **no fabricated receive count, capture socket
or automatic ownership-acquisition collector** in this integration. A read-only metadata/exclusion supplier and memory-only SSH acceptance launcher
now exist; see [READONLY_HARDWARE_ACCEPTANCE.md](READONLY_HARDWARE_ACCEPTANCE.md).
They deliberately leave decoded reception and authenticated ownership unknown.
Until independent physical qualification and supplier provisioning, the path
stays blocked. The Python UsbLeaseGuard provider interface
also supports an in-process measured collector without a receipt file.

Each acquisition independently verifies current kernel ifindex, PHY, sysfs
netdev generation, USB ancestry, UP/native framing/monitor mode, exactly one
netdev on the capture PHY, explicit NM unmanaged/state10, no capture supplicant
binding, one expected service-owned supplicant process, and absence of other
capture owners. Only the currently owned Bettercap PID is allowed. Ethernet
return routing and the original eth0 index/addresses are continuously pinned.
This executor requires Ethernet management; the pure split planner's separate
onboard-Wi-Fi management option is not silently substituted. File assertions
cannot override these kernel/NM/process/route checks.

Loss persists `usb-lease-interlock.json` in the root-owned live state directory
before another launch can occur. Interface-name reuse, index/generation change,
stale ownership, management loss and failed nonempty capture handoff latch
capture off. The process stays alive for diagnostics; a systemd restart loads
the same interlock and expected identity instead of silently adopting a new
adapter. Recovery requires a reaped child and the explicit
`rearm_usb_capture(owner_authorized=True)` integration hook, followed by fresh
qualification. It is not an automatic retry or a hardware-approval bypass.
Normal rotation can reuse only the same independently verified device lease.

Checks occur before spawn and again before Bettercap radio operations, as well
as every tick. Userspace checks cannot atomically lock the kernel's name-to-
ifindex mapping across exec or a REST operation; a field removal-race test is
still mandatory. The startup caplet does not start recon: qualification is
rechecked before recon. No claim of race-free kernel ownership is made.

## BlueZ PAN executor and authenticated agent

`redux/connectivity/bluez_executor.py` provides the asynchronous executor,
`bluez_bus.py` provides a long-lived optional dbus-next client/Agent1 dispatcher,
and `bluez_pan.py` retains pure planning/outcome contracts. The optional
`bluetooth` extra pins dbus-next 0.2.3; dev tests exercise actual message encoding
on fake buses. No Bluetooth package, service or startup unit is deployed here.

`[phone].enabled=false` ships in the private live configuration. Disabled mode
and `--check` do not import/connect the bus. `python -m
redux.connectivity.bluez_executor --config ...` is an explicit future operator
entry point, not a boot service. Private config pins one adapter and one peer
object path plus an IP route probe destination. Addresses, object paths,
passkeys and backend error text are excluded from public status/error output.

Preflight requires a powered adapter with incoming Pairable/Discoverable both
false, measured timeout defaults, exact Device1 Adapter/Address identity and no
existing PAN connection. The executor registers only its application agent;
it does not request default-agent takeover or change power, trust, NM, DHCP,
firewall or application API exposure. PAN is management-only.

Pairing is **outgoing-only**, initiated by an explicit `--pairing` gesture or
`open_pairing(owner_authorized=True)` call on the configured peer. It sets
finite timeout properties but keeps incoming pairing and discoverability
closed. Agent1 accepts only the pinned BlueZ unique sender and peer and an
unexpired numeric-comparison callback. CLI confirmation requires an actual
local TTY; passkeys never go to journals/pipes. PIN, legacy/Just Works,
unknown requests, late approvals and unsolicited peers are rejected. No code
writes Trusted=true. New authenticated bonds wait for a separately established
Trusted property before admission; an external policy/UI must handle that
explicit operator trust decision, not this executor.

An existing trusted bond can be admitted through the explicit
`admit_verified_bond(owner_authorized=True, authenticated_bond_verified=True)`
operator/UI provisioning hook. The caller must supply independent authenticated
bond evidence; neither config nor Device1 Trusted alone sets that proof. The
executor still rechecks Paired/Bonded/Trusted, adapter/address, NAP and object
generation. No bond authentication proof is silently imported on process restart.

Network1.Connect targets NAP with a bounded timeout. Peer security, Connected,
UUID, returned bnep identity and its management route are verified afterwards.
Disconnect schedules capped trusted reconnects without opening pairing; late
success and failed route/identity checks cannot become management_ready.
AlreadyConnected from a racing other client is not adopted or disconnected.

Every RPC verifies BlueZ unique ownership; ObjectManager add/remove events
invalidate peer/adapter generations. Responses from stale generations are
refused. Owner replacement interlocks and closes only this client rather than
mutating the replacement daemon. Cleanup cancels pending pairing, disconnects
only the attempted/owned pinned PAN, restores only its unchanged owned timeout
properties and unregisters its own agent. It verifies the closed baseline;
identity uncertainty or rollback failure reports restoration unverified.
Closing the client releases its exported agent/network ownership. All calls
and operation/cleanup phases are bounded. No uncontrolled daemon/service restart.

Official contracts: [BlueZ Agent1](https://bluez.readthedocs.io/en/latest/agent-api/),
[Network1](https://bluez.readthedocs.io/en/latest/network-api/) and
[dbus-next asyncio bus](https://python-dbus-next.readthedocs.io/en/latest/message-bus/aio-message-bus.html).
PAN association does not prove an authenticated Redux application session or
configure IP addressing. Hardware provisioning must establish those independently;
Redux's existing UI remains localhost-bound.

## Separate DOWN-only mode and image gates

The implemented `--down-only` native VIF profile has a separate capability-
limited backend, no temporary UP or packet path, exact command allowlists,
phase restrictions, singleton/one-attempt behavior and pinned rollback. See
[DOWN_ONLY_VIF_OBSERVATION.md](DOWN_ONLY_VIF_OBSERVATION.md). It has NOT run.
NM 1.42.4 monitor-device exclusion remains preserved in
[NATIVE_VIF_NM_EVIDENCE.md](NATIVE_VIF_NM_EVIDENCE.md); absent enumeration is
still UNKNOWN. The RF-capable profile's activation gate is not weakened.

Assembled-image contracts require the new source payloads, explicit disabled
USB/PAN defaults and existing private config permissions. No image build or
artifact qualification was performed during this source integration.

## Recommended separately approved acceptance gates

1. Read-only USB inventory, power/errors, stable physical identity and Ethernet
   route; establish actual independent monitor ownership and decoded reception.
   Qualify the real evidence supplier; then approve one pinned USB runtime test.
2. Measure startup/rotation, removal during launch/API/poll, reused names,
   management loss and stale observations; prove old-child termination and
   nonempty closed PCAP handoff/ingest/conversion. Verify the persistent interlock
   prevents capture after a service restart; explicit re-arm gets its own scope.
3. Separately provision optional Bluetooth dependencies and a closed adapter,
   pinned phone, independent trust/bond evidence, PAN IP and application auth.
   Approve one bounded outgoing numeric-pair/PAN test with late confirmation,
   timeout, disconnect/backoff, peer/daemon replacement and rollback checks.
4. If native-VIF evidence is still useful, separately approve only the new
   DOWN-only observation and its exact reviewed cleanup. Never reuse earlier
   consumed approvals or infer reception from ARPHRD803.

Offline verification for this integrated source revision: **1,954 passed,
2 skipped** in the full suite; **526 image-contract checks passed**.
Coverage includes driver pre/post-operation lease loss, stale/old-boot receipts,
independent kernel/route/NM/process failures, index reuse, unkillable children,
handoff failure, restart interlocks, real D-Bus message encoding on fake buses,
agent sender/expiry denial, trust revocation, late replies, owner/object changes,
partial PAN/property errors, reconnect timeout, other-client races, image disabled
defaults, and DOWN-only command/AST/rollback restrictions. These are software and
synthetic fixtures, not RF, PAN or physical rollback acceptance evidence.

The read-only toolkit adds strict receipt-schema validation and assembled-root
requirements for the collector, acceptance module and PAN preflight. Its source
tests are synthetic; no physical USB/PAN qualification is implied.
