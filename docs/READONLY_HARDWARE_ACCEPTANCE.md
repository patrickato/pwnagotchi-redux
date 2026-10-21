# Read-only hardware evidence and acceptance toolkit

Source-only tooling; **no Pi execution, installation, RF qualification, USB
hotplug, PAN provisioning or image build is implied by its tests**. Existing
installation, comparison builds and Jayofelony card stay untouched. This toolkit
does not consume or renew any previous hardware approval.

## One command from the trusted laptop

```sh
python3 scripts/pi-acceptance.py --host <independently-verified-Pi-IP>
```

Uses the existing `~/.ssh/redux_field_ed25519` identity without reading it into
Python, strict ED25519 host-key verification, BatchMode and bounded connection/
keepalive settings. A changed host key stops the command; obtain console proof,
never bypass it. The source bundle is imported entirely in Pi process memory
with `sudo -n python3 -B -`, using the server's SSH_CONNECTION client address as
the Ethernet return-route destination. No source installation, bytecode, receipt,
log retrieval, capture opening or persistent Pi artifact. The laptop uses an
anonymous temporary stdin file and removes it on close. No SSH key is bundled.

Installed-source equivalent: `python3 -B -m redux.acceptance --management-peer
<verified-management-peer-IP>` (packaged console alias `redux-acceptance`). Do not
install it on the Pi merely to run acceptance: use the memory-only launcher.
Read-only execution is permitted under the workbench rules; any installation or
receipt provisioning still requires separate Pi-change approval.

One process, no retry: 45-second outer SSH deadline, 35-second shared command
budget on the Pi, two seconds per diagnostic command and a hard 128-KiB combined
output limit. Timeout/overflow kills and reaps the read-only child. Only fixed
commands/property reads are allowed; no shell on the Pi collector, D-Bus
activation, scan, packet socket, agent registration or state-setting command.
Exit 0 means its measured gates passed; 2 means FAIL/UNKNOWN remains; 3 means SSH
or the diagnostic response was unavailable. A zero SSH process status is never
interpreted as hardware qualification. Unknown facts remain explicit.

The report checks Pi 4/5 and ARM64 identity, kernel/module vermagic and loaded
srcversion, existing global US regulatory domain, dedicated writable ext4 capture
mount/free space, Redux unit states, Ethernet return route, all Wi-Fi netdevs,
measured USB ancestry, real ARPHRD, advertised monitor capability, NM state,
process conflict indicators and capture-lease eligibility. A separate bounded
sysfs inventory counts USB device descriptors including hubs; it reads no
serial/VID/PID and cannot identify an unbound Wi-Fi adapter as functional. US is the current
field target, not permission to change country or a universal travel default.
Module vermagic/srcversion are compatibility evidence, **not module SHA256/image
provenance verification**; use the signed-image acceptance checks for that gate.
Free space is a snapshot, not storage endurance or filesystem-integrity proof.

Reports contain ordinal radio labels, bounded driver names, booleans, enums and
counts only. Interface names/PHYs, IP/MAC/SSID, USB serial/path, boot UUID, PIDs,
BlueZ object paths, credentials and backend error text are not published. No
capture content, caplet or journal is read. Missing/inaccessible measurements
cannot become PASS. An inventory failure discards all partial radio results.
The collector deliberately does not reuse historical Doctor/event counters as
RF evidence; existing sanitized `redux-report --summary` remains the separate
capture/ingest/Doctor/TFT report.

## Independent USB receipt supplier

`redux.radio.local_evidence.collect()` reads sysfs netdev inode/index/PHY/type/
flags, real device ancestry and subsystem links, explicit nl80211 `iw phy`
Supported interface modes and bands, every `iw dev` interface (no PHY collapse),
NM managed/state, and bounded process names/start times/cgroups. No driver
allowlist supplies monitor support. An absent Supported interface modes attribute
invalidates the observation; absent NM enumeration is UNKNOWN, never unmanaged.
SDIO ancestry supplies onboard attachment; other unknown buses stay unknown.
Driver names and USB device identifiers cannot prove reception or authorization.

Technical USB exclusion checks reuse the live verifier's independent kernel,
NM no/state10, supplicant binding/process and competing-owner checks through the
allowlisted bounded reader. A running Bettercap is **never exempted by a caller
PID**. Positive process-name/cgroup exclusion is still not authenticated exclusive
ownership: the receipt's owner remains unknown/owner_verified=false. Likewise
`decoded_frames=null` always. All generated receipts therefore fail the live
lease's receive qualification. This is intentional truthful evidence, not a
metadata-based route around the existing safety gate.

Collection is one attempt with a START monotonic timestamp, a maximum three
seconds, current boot identity, before/after full radio inventory, iw identity,
NM state, relevant known-owner process start identities and Ethernet
address/index/source-route checks. Unrelated process churn is not mistaken for
a capture-owner transition. Removal,
index/physical-path/inode reuse, process replacement, boot change, changed mode,
permissions and deadline failures abort instead of producing a partial receipt.
Userspace before/after reads cannot exclude a remove/re-add between snapshots
that recreates identical facts, nor authenticate all possible hostile processes.
No race-free kernel ownership claim is made.

The exact FileLeaseEvidence schema remains documented in
[LIVE_CONNECTIVITY_GATES.md](LIVE_CONNECTIVITY_GATES.md). The reader now validates
strict booleans/integers, finite monotonic timestamps, identities, duplicate
names/indices and bounded native field types. No truthiness-based qualification.

`publish(receipt, absolute_path)` is an explicit future provisioning API, **not
called or exposed by acceptance**. It requires root, current boot and age <=3s,
1..32 valid radio rows, no manufactured decoded count, trusted root-owned
nonwritable directory ancestry (root sticky ancestors such as /tmp are allowed),
no symlink directory and a regular root-owned non-writable existing destination.
It writes a unique 0600 temporary receipt, fsyncs, rechecks freshness, atomically
renames through a pinned directory descriptor, then fsyncs the directory.
Pre-rename failure removes only its own temporary and preserves the previous
diagnostic receipt. A directory-fsync failure after rename reports
receipt_publication_uncertain: the new file may already be visible; no blind
rollback overwrites another writer. Old evidence naturally expires under the three-second live guard;
a failed refresh must never be treated as a new sample. No daemon/timer or live
lease configuration is installed/enabled. Even a successful receipt write is a
Pi mutation requiring explicit approval and does not grant capture authorization.

## PAN preflight, with no provisioning

Optional private launcher arguments:
`--pan-peer <pinned-Device1-path> --pan-route-peer <existing-phone-IP>
--pan-adapter <pinned-Adapter1-path>`. No device discovery or address enumeration.
Omit them and every PAN gate is UNKNOWN; Bluetooth stays untouched.

Read-only bus calls disable activation and pin the BlueZ unique owner. Adapter
power/closed Pairable/Discoverable flags, exact peer Adapter/Address, Paired,
Trusted and Bonded are measured twice. Network1 absence remains unknown;
Connected/UUID/bnep identity and existing IP route/source address/interface UP/
index are separately checked. Changed peer/owner/properties/routes discard the
observation. No agent, Connect, Pair, Trusted write, DHCP, listener or network
configuration occurs; no rollback mutation is appropriate for read-only checks.

Each gate is independent: **paired -> trusted -> bonded -> independently
authenticated bond -> Network1 connection -> verified IP return route ->
authenticated Redux application session**. Earlier properties never imply later
ones. Independently authenticated bond and Redux session stay UNKNOWN: this
collector has no independent pairing transcript or authenticated application
challenge. Object-generation verification also stays UNKNOWN: two property
snapshots cannot rule out a same-path remove/re-add; the qualified executor's
long-lived ObjectManager generation tracking is a separate acceptance gate.
No preflight result enables the disabled-by-default PAN executor.

## Separately approved future procedures — disabled and NOT executed

These are reviewable procedures, not executable acceptance flags. **No hardware
execution flag exists in the read-only entry point.** Stop before any physical
step until its exact adapter identity, reviewed runner, deadline and cleanup
have been approved. No generic metadata receipt can bypass that approval.

### Before A: adapter availability

The current read-only field observation enumerates no USB Wi-Fi adapter. The
next physical approval would be **one supervised connection of an identified
USB Wi-Fi adapter, followed only by read-only inventory**, with console and
Ethernet preserved. Review USB power and existing NM auto-management policy
first: insertion itself may trigger the OS's existing device policy. No
unplug/replug race, NM/mode changes, live restart or RF qualification is included.
Do not infer permission to plug in hardware from permission to run this toolkit.

### A. Genuine USB decoded reception

Request one supervised, passive-only qualification of one locally pinned USB
adapter on the disposable Redux Pi, with console available and strict Ethernet.
Record baseline image/kernel/module digest, US, captures/storage, original
interfaces/ifindices/inodes/USB ancestry, NM/supplicant/process ownership and
service states. Do not proceed if USB hardware is absent or any ownership,
management or rollback fact is uncertain. Prepare and review the exact per-device
runtime isolation/mode/UP changes before approval; there is deliberately no
universal automatic NM/radio preparation in this toolkit.

After explicitly approved isolation, stop only previously active redux-live if
needed, authenticate/pin the sole test owner, verify native ARPHRD801/803,
monitor mode and stable Ethernet. For **at most 60 seconds**, receive passively
on that pinned netdev and independently decode actual frame headers, validating
radiotap lengths and 802.11 frame control/header bounds. Publish aggregate
valid/invalid counts and link type only; no identifiers, packet payloads, active
RF, scans, deauth, cracking or uploads. Prefer memory-only processing with no
persistent capture. A mode label, advertised support, RX counter, or merely
opening a packet socket cannot pass decoded reception. Receipt freshness and
physical identity must be independently rechecked; historical qualification is
not a live observation.

Close/reap the test owner first. Restore only approved original runtime NM/mode/
UP and previously active live state, using identity-pinned commands; independently
verify original Ethernet/US/interface/NM/service state. Identity loss or partial
cleanup stops further changes and reports restoration UNKNOWN/FAIL. Never guess,
retry, reload, globally restart managers, reboot or reflash. A second attempt or
expanded recovery requires fresh approval. The existing native DOWN-only VIF
procedure remains independent and is NOT part of USB reception approval.

**Source-only follow-up:** the bounded AWUS036ACM/mt76x2u runner is now
implemented with synthetic tests; see [AWUS036ACM_PASSIVE_QUALIFICATION.md](AWUS036ACM_PASSIVE_QUALIFICATION.md)
for its strict pre-isolated baseline, exact commands and separate NM companion
procedure. It is not part of the read-only entry point and has NOT been executed.
The loaded live runtime must already pin wlan0; unknown/auto-selection blocks
execution. No hardware approval, fabricated counts or lease interlock bypass
is supplied by source completion.

### B. USB live runtime, handoff and removal (separate later approval)

Only after A and a separately qualified independent ownership/reception supplier:
review one adapter-pinned live lease test and explicit temporary configuration
rollback. Approve exact live stop/start/configuration steps separately. Confirm
no capture fallback onto the management radio. Observe one startup and one
rotation, old-child termination, closed nonempty capture handoff, ingester ledger
and successful conversion on operator-owned test traffic. Captures remain
private/local; deletion is not implicit in approval.

Removal during spawn/API/poll and same-name replacement are **separate supervised
cases** with approved operator unplug/replug times, bounded observations and no
automatic retries. Expect persistent interlock, no replacement adoption, no
service-restart capture bypass and preserved Ethernet; unkillable old owner or
handoff failure must block new capture. Explicit re-arm and service restart each
need named approval. Stop on failed gate; report exact remaining state and
rollback uncertainty rather than attempting new recovery. Brownout and storage
stress do not belong to the first live test.

### C. Phone PAN provisioning and application qualification (separate approval)

First use the read-only preflight. Review optional package deployment, closed
adapter, fixed phone identity and physical numeric-comparison confirmation with
a finite window. Obtain explicit approval for one outgoing pairing/PAN operation
and its owned rollback, plus any separate operator trust action; never silently
grant trust or adopt a preexisting connection. Independently preserve the
security proof for an existing bond; Paired/Bonded/Trusted alone is insufficient.

Approve any needed IP provisioning and Redux application exposure separately.
Verify bnep generation, expected phone return route and an authenticated Redux
challenge/session independently of Network1. Then separately test bounded trusted
reconnect, trust revocation, daemon/peer replacement, late callbacks and partial
rollback. No incoming pairing, default agent takeover, network manager restart,
listener exposure or package installation is authorized by this document.

## Read-only Pi 4 checkpoint — 2026-10-17 16:53:55 UTC

The checkpoint memory-only source bundle was exercised over the previously
console-verified strict Ethernet SSH connection. Bundle SHA256:
`aa840fe5d9be608405e9b9bf6de1d19bd9bdeb476cbf49cb527779d99adb45c0`.
No Pi files, services, ownership, radio/driver, Bluetooth or power state changed.

| Gate | Observation |
|---|---|
| Pi/architecture | PASS Pi 4/5 model check and ARM64 |
| Kernel/module | PASS installed vermagic and loaded srcversion agreement; no new artifact provenance claim |
| Ethernet | PASS return route, bound source address and stable interface/address snapshot |
| Country/storage | PASS existing US, dedicated writable ext4 captures, >=256 MiB free |
| Services | PASS redux, redux-live and capture-ingest timer active |
| Radio inventory | Fresh; one positively SDIO/onboard brcmfmac radio; no USB Wi-Fi netdev |
| Native framing | FAIL native link prerequisite: monitor label and advertised monitor support, actual ARPHRD1 |
| NM | Explicit managed=true, state20; not capture-exclusive ownership |
| Process indicator | One known competing capture-process indicator; not a claim of unauthorized activity |
| Reception/ownership | UNKNOWN decoded reception and independently authenticated exclusive owner |
| USB lease | FAIL eligibility; no external adapter and no genuine receive/ownership qualification |
| PAN | UNKNOWN; no private pinned phone supplied, no Bluetooth bus queried |

Exit 2 correctly reports incomplete acceptance. The absence of a USB **Wi-Fi
netdev** does not prove there is no electrically attached USB device: an unbound
adapter can lack a netdev. A separate generic USB device count (hubs included) was subsequently added;
that count is synthetic-tested and was not measured in this checkpoint. No
serial or identifier enumeration is exposed.
Initial integration exposed overly broad process-churn detection; the collector
now pins relevant known owner generations rather than rejecting unrelated short-
lived processes. Synthetic regressions still reject capture-owner PID reuse.

Offline verification on the integrated workbench (including concurrent touch
calibration changes): **2,051 passed, 2 skipped**; **529 hardware-free image
contract checks passed**. Tests cover bounded subprocess timeout/output/reaping,
strict command denial, stale/nonfinite observations, missing capabilities/NM,
netdev replacement even with reused index, PID reuse, boot/source-route changes,
permissions, exact reader/writer schema, atomic publish failures and post-rename
uncertainty, absent/replaced PAN properties/owner, independent security gates,
privacy, and memory-only isolated imports. These synthetic fixtures do not
qualify USB RF, Bluetooth, or physical rollback. No new image was produced.
