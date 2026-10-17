# AWUS036ACM passive header qualification — review only

**Source implemented and synthetic-tested. NOT executed on a Pi. No hardware
approval is currently active.** No source deployment, RF test, NM/radio change,
service restart, unplug/replug, driver operation or image build occurred.
Parallel touchscreen work is preserved on human/redux-workbench.

## Exact scope and limits

Target one physically operator-identified Alfa AWUS036ACM, measured USB
0e8d:7612 and bound driver mt76x2u, distinct from the SDIO brcmfmac PHY.
The shared USB ID also names other products; the hardware label/port must be
verified locally. No driver allowlist or monitor advertisement proves reception.

Runner: `python3 -B -m redux.radio.awus036acm --iface <private-USB-netdev>
--management-peer <verified-Ethernet-SSH-client-IP>`. Packaged alias:
`redux-usb-qualify`. **Default is preflight only.** Future execution additionally
requires `--execute-approved --seconds 30`, after approval of exact reviewed
source, named adapter, isolation and rollback. No installed source is assumed:
source deployment or a reviewed memory-only transport needs its own approval/
review; this iteration does not copy code onto the Pi. The existing read-only
acceptance launcher remains read-only and does not import or run this executor.

This is deliberately **not a general NM ownership-acquisition runner**. It
accepts only a pre-isolated USB baseline: explicit NM no/state10, managed mode,
DOWN, ARPHRD1, no current frequency, no supplicant binding/control socket and no
foreign packet owner. It never changes NM, creates a VIF, stops services, changes
onboard mode/channel, reloads modules, scans, transmits, persists a capture or
issues a lease receipt. Unsupported baseline -> STOP; do not loosen the gate.

Ethernet stays management; onboard wlan0 stays monitor/UP under its existing
Bettercap. The running supervisor must **already** have loaded a root-private
configuration explicitly preferring wlan0, with connected-capture override and
USB lease mode off. Its module/config arguments, config age before process start,
config digest, root Bettercap executable/start identity/cgroup/`-iface wlan0`,
AF_PACKET inode bindings and both process generations are verified. Merely
observing today's Bettercap PID on wlan0 cannot pin its NEXT rotation. An empty
preferred_iface, a recently edited file or an unknown loaded configuration blocks
the test. The runner will not edit config or restart live to make this gate pass.

## Before: review and separately approve isolation

1. Console available; strict host-key-verified Ethernet SSH with the previously
   verified identity. Check the actual SSH_CONNECTION client route is eth0 and
   pin its ifindex/inode/addresses and source address. Never substitute Wi-Fi.
2. Use read-only acceptance and signed-image records to verify Pi 4/5 ARM64,
   correct image/kernel/installed and loaded modules, healthy captures storage,
   existing US, unblocked RF and unchanged Redux services. This runner checks
   platform, boot, US, route and protected radio/runtime itself, but does not
   replace full image SHA256/driver provenance/storage/rfkill acceptance.
3. Identify the exact adapter privately through sysfs USB ancestry, descriptors,
   USB device number/generation, driver and iw PHY/netdev/ifindex. No other VIF
   on either PHY and no third Wi-Fi netdev. Check advertised monitor support on
   that PHY. Pin original Ethernet/onboard identities/mode/channel/NM flags and
   live/Bettercap generations. Any source rotation during the trial aborts; do
   not postpone/force a runtime rotation or restart it.
4. Verify loaded live config already pins wlan0 as above. **If auto-selection is
   still configured, STOP before isolation.** A future config pin/restart needs
   separate specific approval, because the current instruction preserves live.
5. If USB is already explicit NM no/state10, managed/DOWN/ARPHRD1/no frequency,
   no external isolation command is required. Save that original private state;
   runner rollback restores exactly this baseline.
6. Otherwise the *only proposed companion isolation*, needing named approval,
   is for original NM yes with disconnected/unavailable state20/30, managed/DOWN,
   no frequency, no USB IP/route/connection and **existing per-device
   Device.Autoconnect=false**. Measure original ownership/state/autoconnect and
   identity first. No active connection is disconnected to make the test fit.
   Behind a fresh pinned-identity/Ethernet/onboard/runtime gate, run **once**:

   ```text
   nmcli --wait 5 device set <pinned-USB-netdev> managed no
   ```

   Verify exact same USB identity, no/state10, managed/DOWN/ARPHRD1/no frequency,
   unchanged autoconnect=no, no supplicant binding and preserved protected
   baseline. Unknown NM enumeration never counts as no/state10. Failure stops;
   no runner activation and no guessed recovery. The read-only runner preflight
   must now pass. No device.autoconnect setter, persistent profile/udev change,
   global manager restart or onboard NM change is part of this proposal.

The companion NM change and its final return are **documented for supervised
review, not implemented as implicit mutations**. Actual original ownership and
policy have not been measured for an attached adapter. Therefore no blanket
"ready for hardware execution" claim is made.

## Approved runner sequence (future only)

All commands use argument arrays without a shell, two-second subprocess bounds,
128-KiB combined output limits, an exact per-adapter mutation allowlist and
single-use phase guards. A cooperative abstract AF_UNIX lock permits one trial
process; an instance cannot retry. The execute flag expresses operator intent,
not a durable approval token or permission to rerun a consumed attempt.

From verified pre-isolated baseline:

```text
iw dev <pinned-USB-netdev> set type monitor
iw dev <pinned-USB-netdev> set monitor none
iw dev <pinned-USB-netdev> set freq 2412 HT20
ip link set dev <pinned-USB-netdev> up
```

Verify identity/NM/protected baseline after each command, monitor mode, native
ARPHRD801 or803 and measured 2412 MHz before receiving. This first profile is
US channel1/HT20 only: no hopping, arbitrary channel or monitor-active flag.
A driver rejecting a DOWN-state channel command is a failed hardware gate;
do not automatically reorder commands or retry. Header reception elsewhere or
on an idle channel remains unknown rather than manufactured success.

One AF_PACKET/SOCK_RAW socket is created with **protocol zero**, outgoing
packets ignored, bounded receive buffer, then bound to only the pinned netdev
(protocol3 in Python host order). Before counting any packet, kernel
`/proc/net/packet` inode->ifindex binding must match. Bettercap packet sockets
are permitted only when their inodes belong to the pinned Bettercap process
and bind the onboard index. Wildcard and foreign packet sockets stop the trial.
No promiscuous membership, TX function, API command or packet file is used.

For a configured window >0 and <=60 seconds (default30), validate each packet's
incoming direction, netdev, real packet ARPHRD, truncation status and headers.
Bounds: 8192-byte datagram, radiotap<=512 bytes/four presence words, 20,000
packets and 32MiB processed. Unknown radiotap namespaces/fields and variable
802.11 extensions are unsupported, not successful frames. Validate radiotap v0,
length/alignment/field bounds, Flags and RX/TX metadata; reject bad FCS/PHY or TX
metadata, and validate CRC32 when an FCS trailer is explicitly present. Validate
802.11 protocol version/type/subtype, DS, QoS/four-address/HT header lengths and
conservative control layouts. No address, SSID, payload, passkey or raw error
text is emitted; no packet persists. Unsupported/truncated traffic cannot pass.
Absent FCS is **not** proof of FCS validity; these are independently received
header counts, not full payload or handshake authentication.

Identity is checked per datagram, protected ownership/route/runtime at least
once per second of processing and before/after sampling. Whole-trial main limit
120 seconds, cleanup20 seconds. The CLI installs SIGALRM and converts INT/TERM/
HUP to bounded cleanup. The receive alarm starts **before socket creation**;
close is attempted once before post-observation/rollback. A small closing margin
is reserved inside the requested window; an observed elapsed-window overrun
fails qualification. Kernel stalls, SIGKILL, power loss and scheduler latency
cannot be made safe by a Python deadline: never claim restoration without the
final independent gate. Queued late frames are discarded.

Userspace checks are not an atomic kernel PHY lock. A state change between
snapshots remains a hardware race gate. This source neither authenticates an
exclusive live lease owner nor automatically transfers qualification into
FileLeaseEvidence. Existing stale/removal/rearm interlocks stay unchanged.

## Only runner rollback, once per needed command

First close the receive socket and verify close. Re-read physical identity,
explicit NM exclusion, process/socket ownership and Ethernet/onboard invariants.
If USB identity is missing/replaced, ownership changed, socket close is uncertain
or another required gate cannot be measured: refuse mutation and report exact
remaining verified predicates; do not delete a device or try another identity.

If the same approved USB is still UP:

```text
ip link set dev <pinned-USB-netdev> down
```

Verify DOWN. If still monitor, restore only:

```text
iw dev <pinned-USB-netdev> set type managed
```

Require exact original USB identity, managed/DOWN/ARPHRD1/no frequency and
explicit NM no/state10; compare full original Ethernet/US/onboard state,
config digest and live/Bettercap/supplicant generations. If mode restoration
retains an unexpected frequency or runtime rotated, full restoration fails;
no extra channel setter/service action is authorized. Partial command failures
record intent first and permit only this identity-pinned cleanup. Unknown state
never becomes restored=true. A planned bounded read-only remaining-state snapshot
reports identity verification, mode, UP, ARPHRD and NM exclusion after a cleanup
failure; it never retries cleanup or upgrades the failed restoration result. Never retry, unplug, reload, globally restart,
reboot, flash or change configuration to repair a failed gate.

`restored=true` covers **the pre-isolated adapter/protected baseline only**.
`original_nm_return_verified=null` explicitly prevents claiming that an earlier
external NM isolation was reversed. Header qualification is true only for
positive valid incoming header counts, no operation failure and verified runner
restoration. FAIL/UNKNOWN and last-gate predicates are sanitized and bounded.
Exit0 qualified; exit2 unqualified with verified baseline restoration; exit3
restoration unverified. Default read-only preflight may exit2 even when ready.

## Companion return to original NM ownership (separate reviewed step)

Only if step6 changed original NM yes, and runner socket closure and ALL baseline
restoration/protected gates pass, independently recheck original USB identity,
managed/DOWN/ARPHRD1/no frequency/no IP/no binding, current no/state10,
**autoconnect still no**, protected runtime/Ethernet/US and original state record.
Then the specifically approved companion rollback is one command:

```text
nmcli --wait 5 device set <pinned-USB-netdev> managed yes
```

Verify original managed=true/state20 or30 **matching the saved baseline**, same
USB state, autoconnect=no, no new binding/IP/route, unchanged onboard/Ethernet/
runtime and no packet owner. This is not an auto-connect or original-connection
restorer. If any prerequisite or response is uncertain, STOP and report NM
ownership restoration unverified; no further changes. If the adapter began
unmanaged, this command is forbidden. The runner does not issue or verify this
companion command, so an operator must record that independent final result.

## Remaining physical gates

The adapter is not yet measured, and loaded runtime preference, per-device NM
policy, DOWN-channel support, actual headers/FCS behavior, unchanged onboard
capture and final baseline/companion restoration remain unverified. Physical
insertion itself needs approval because existing NM policy can act on it.
Only after those gates may a separate reviewed USB live lease/handoff/removal
qualification be proposed. No live runtime USB switch or release image belongs
to this passive test.

Primary references: [Linux v6.12 mt76x2u USB table](https://raw.githubusercontent.com/torvalds/linux/v6.12/drivers/net/wireless/mediatek/mt76/mt76x2/usb.c),
[Radiotap layout/alignment](https://www.radiotap.org/),
[Radiotap Flags](https://www.radiotap.org/fields/Flags.html),
[CPython3.11 AF_PACKET binding](https://raw.githubusercontent.com/python/cpython/v3.11.0/Modules/socketmodule.c).

## Offline verification

Integrated workbench including parallel touchscreen/capture panels: **2,265
passed, 2 skipped** in the full suite; **644 hardware-free image checks passed**,
including **107 qualification/parser/backend regressions**. Synthetic coverage
includes malformed/aligned/extended radiotap, FCS errors, variable 802.11 header
bounds and fuzz input; partial setup/open/close, one-attempt phases and direct
command bypass refusal; replaced USB/index/PID/config/NM/socket ownership;
receive alarms/budgets, late frames, failed/uncertain rollback and sanitized
remaining-state reports; default CLI nonexecution and required image payloads.
These counts are software evidence, not measured RF reception or physical
restoration. The source-generated image contracts add no autostart configuration.

## Current read-only readiness finding

The Alfa is now present and the running Bettercap actually owns it; NM Managed
and Device.Autoconnect are both true. The onboard assignment and isolated USB
preconditions fail. See [DUAL_RADIO_READINESS_20261017.md](DUAL_RADIO_READINESS_20261017.md)
for measured facts and the separate configuration-handoff/policy boundaries.
The original runner gates remain unchanged; do not run it in the current state.
