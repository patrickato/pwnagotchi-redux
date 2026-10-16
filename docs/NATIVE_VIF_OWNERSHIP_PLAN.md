# Raspberry Pi 4 native-monitor ownership isolation — approval request

**Status: REVIEW ONLY — NOT AUTHORIZED TO EXECUTE.** This is the next
candidate experiment after the 2026-10-16 read-only ownership stop. It changes
the running disposable Redux field card if and only if the owner grants
**separate, explicit permission** for the sequence below. The original
Jayofelony SD card must remain untouched.

## Measured problem, not assumptions

The signed `4f907ff` Pi 4 build boots, verified over strict Ethernet SSH.
The installed/loaded patched brcmfmac evidence, REDUXCAP mount, US country,
services and Bettercap authentication passed. Only `wlan0` was present;
`iw` labeled it monitor while kernel ARPHRD=1 and firmware monitor=0.
No captures or handoffs appeared across a 313-second interval.
NetworkManager reported `wlan0` **managed=yes, state=20 (unavailable)**;
`wpa_supplicant` is active. No per-interface supplicant socket was seen.
These facts do **not** prove exclusive ownership, nor do they prove that NM
is currently reconfiguring `wlan0`. The native monitor VIF test **never ran**.
See [FIELD_DIAGNOSTICS_20261016.md](FIELD_DIAGNOSTICS_20261016.md).

## Proposed minimum-change test (one attempt, bounded)

A. **Read-only inventory and abort conditions.** Use only the previously
console-verified SSH host key over `eth0`; verify SSH return routing uses
`eth0`, Pi 4 identity, pinned image/module, attached Wi-Fi PHY, existing
interfaces, `redux-live`/Bettercap ownership, configured/global US, unblocked
radio, and REDUXCAP identity. Collect sanitized NetworkManager **runtime**
ownership for `wlan0` (do not print connection names/IDs, SSIDs or profiles)
and independently check whether a supplicant instance actually controls
`wlan0` (process, control interface and/or D-Bus evidence). Document actual
version/syntax of `nmcli`. **STOP** if Ethernet routing is uncertain, a
non-Redux process owns the radio, the NM state changes unexpectedly, the
driver is already busy, or identity/storage/country mismatches baseline.

B. **Isolation, only after specific approval.** Stop **only**
`redux-live.service` using a supervised, strictly time-limited session.
Verify its Bettercap child released the interface. Do **not** stop or restart
NetworkManager globally, stop `wpa_supplicant` globally, alter `eth0`,
change connection profiles, change regulatory country, reload modules, or
edit udev/NetworkManager config. If `wlan0` was initially NM managed=yes,
the candidate **per-device runtime-only** operation is
`nmcli device set wlan0 managed no` (no `--permanent` flags).
The NetworkManager reference specifies this updates runtime state by default,
without changing connection profiles. Immediately check the new unmanaged
state, existing WLAN interfaces, supplicant claims and unchanged `eth0`.
If runtime isolation isn't confirmed, **STOP and restore baseline**.
Do **not** automatically disable additional managers or force cleanup.

C. **Native monitor VIF trial, only if isolation is actually established.**
Create **one collision-free, temporary VIF** on the measured Wi-Fi PHY using
the installed driver/kernel-supported interface-add path; never simply
relabel the primary `wlan0`. Verify the new netdev is distinct from `eth0`
and `wlan0`, activate it once, measure actual ARPHRD (expected 801 or 803)
and firmware monitor mode (expected 2 for this Nexmon native monitor path).
Probe no more than **60 seconds of passive received IEEE 802.11 frames**
with locally decoded link-layer evidence and only aggregate/sanitized counts.
No injection, deauth, targeted RF action, cracking, SSID/MAC logging,
raw frame upload or persistent PCAP. Mode/packet counts must be physically
measured; `iw type monitor` alone does not pass. Unexpected errors stop
the trial; no repeated VIF/driver attempts.

D. **Narrow rollback while Ethernet is verified.** Bring down/remove only
the **known temporary VIF** if it exists; do not touch unrelated devices.
If this trial changed only `wlan0`'s runtime NM flag from managed=yes,
restore it with `nmcli device set wlan0 managed yes`. Verify the original
`wlan0` and per-interface NM/supplicant ownership state (not just command
exit), no temp VIF, original route via `eth0`, US, and noncrashed driver.
Restore `redux-live.service` to its originally active state **only when
baseline interface and ownership conditions are safe**. Verify fresh
checkpoint and no unexpected errors. If any rollback verification fails,
**STOP**, preserve sanitized observations and ask for a new instruction;
never force-driver removal, global manager restart, radio reset or reboot.

E. **Report distinct phases.** Publish BEFORE / ISOLATION / TEST / AFTER
PASS/FAIL/UNKNOWN gates, actual link type + firmware monitor mode, decoded
frame count (if tested), and exact restoration status without identifiers.
No claim of native reception if only a VIF or a mode label was observed.
Do not treat an unfinished cleanup as a clean restored baseline.

## Guardrails and time/rollback risk

- A runtime NM change is **not guaranteed** to make the device available to
  the test: verify after it happens. NM `state=20` alone is not enough.
- Even temporary NM/VIF operations can transiently change local Wi-Fi state,
  and firmware/driver errors can prevent perfect restoration. Keep
  authenticated local-console recovery available and a real Ethernet path;
  **do not rely on a shell trap as the sole rollback plan**.
- Codex should prepare the exact command/stop conditions and bounded
  cleanup sequence **offline** before requesting the owner's approval. This
  document does not authorize writing or scheduling rollback tasks on the Pi.
- If the supplicant actively owns `wlan0`, **do not proceed**. Stopping
  supplicant is outside this plan and requires its own reviewed approval.
- No new build/reflash is justified until measurements reveal a source/driver
  defect that a build can actually correct.

**Authoritative NetworkManager semantics:** official
[nmcli device set](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html)
documentation distinguishes runtime `managed` from `--permanent`, and
[NetworkManager.conf](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/NetworkManager.conf.html)
documents separate persistent/unmanaged policies.

## Coordination

Codex exclusively owns the field execution and local WSL baseline; the
chat-side lead owns source-only UI/integration work. Work on
`human/redux-workbench`, pull/reconcile before commits, no main merge.
A new approval should name this **one monitored NM-per-device/VIF attempt**
and no broader service/radio changes.

## Prepared exact runner — 2026-10-16 (still awaiting execution approval)

Local source: `scripts/native-vif-isolation.py`; synthetic regressions:
`tests/test_native_vif_isolation.py`. The default invocation performs only
read-only preflight. Only `--execute-approved` selects mutating operations;
that flag must not be used without the owner's new specific permission.
The agent has **not** used it. No file is installed on the Pi. Execution
uses a process-lifetime abstract Unix socket to reject overlapping trials;
no lock file or daemon is created. D-Bus reads disable service auto-start.

### Remaining ownership evidence, measured read-only

- Installed nmcli is **1.42.4**. Its `device set --help` (help is printed on
  stderr) and packaged manual confirm `managed {yes|no}` device syntax.
- NetworkManager's D-Bus properties independently confirm wlan0 Managed=true,
  State=20, and no ActiveConnection. These are runtime observations, not
  connection-profile names or SSID data.
- The supplicant's D-Bus Interfaces property is an empty array; its wlan0
  control socket is absent. Process inventory showed only the expected
  NetworkManager and wpa_supplicant service instances, plus Redux's Bettercap.
  Thus the active supplicant daemon is **not currently bound to wlan0**.
- The prepared runner's default read-only preflight returned
  `prerequisites_satisfied=true`: strict Ethernet return route, original
  eth0 address/interface identity, Pi/image/kernel/module, original wlan0
  identity, US, RF blocks, REDUXCAP identity/free space and fresh live state
  pass. NM's managed claim is expected at baseline; changing that claim is
  precisely the approval-gated isolation operation, not an already passed
  runtime isolation test.
- A new VIF's manager ownership, firmware activation, reception and actual
  rollback remain **UNKNOWN until approved execution**. Preflight is rerun
  immediately before the first mutation; any changed prerequisite aborts.

### Exact commands and verification sequence

The fixed candidate name is `rxv5e16a901`, on the currently observed `phy0`.
A collision with **any** existing netdev aborts; no name recycling or retry.
The runner records the new netdev's ifindex/PHY/mode and refuses deletion
if its identity changes. The primary wlan0 is never relabeled or toggled.

| Order | Command (after approval only) | Required follow-up |
|---|---|---|
| 1 | `systemctl stop redux-live.service` | Service inactive, Bettercap gone, Ethernet/US/storage/driver unchanged, supplicant still unbound |
| 2 | `nmcli --wait 5 device set wlan0 managed no` | Managed=false and NM state=10; no supplicant claim; no other WLAN interfaces/process owner |
| 3 | `iw phy phy0 interface add rxv5e16a901 type monitor` | Exactly one new monitor netdev, correct PHY/ifindex, ARPHRD 801/803, firmware still 0, no NM or supplicant claim on it |
| 4 | `ip link set dev rxv5e16a901 up` | Same device identity, ARPHRD 801/803, firmware monitor=2, isolation and baseline safety intact |
| 5 | Receive-only Python AF_PACKET socket bound to that VIF | At most 60 seconds / 10,000 received frames; aggregate 802.11 MAC-header type counts, no payload/address output or packet file |
| Cleanup 1 | `ip link set dev rxv5e16a901 down` | Attempt once on the identified test netdev only |
| Cleanup 2 | `iw dev rxv5e16a901 del` | Attempt once; require original interface set, original primary ifindex/type, firmware 0, Ethernet/US/storage and noncrashed driver |
| Cleanup 3 | `nmcli --wait 5 device set wlan0 managed yes` | Only if the trial attempted the NM change and the current flag needs restoration; verify original managed=true/state20, primary UP flag and no supplicant binding |
| Cleanup 4 | `systemctl start redux-live.service` | Only if originally active and now inactive, after safe radio/ownership restoration; require a new fresh running checkpoint, no current failure/error, original Doctor health and Ethernet/US |

All subprocess calls have a six-second timeout; the NM commands also use
`--wait 5`. Main phase deadline is 210 seconds, followed by at most 90 seconds
for bounded cleanup. Cleanup never creates another interface, reloads a
module, changes country/channel, edits a profile/configuration, or restarts a
global manager. If stopping/setting/creating partially succeeds before an
error, recorded intent triggers the same narrow cleanup; commands are not
blindly repeated. Every cleanup command failure is retained in the report; restoration is
claimed only if the original measured baseline is subsequently verified. Unexpected route/country/driver/interface identity changes
prevent the later NM/service restoration steps when those would be unsafe.
There is no direct `nexutil -m` setter or primary-interface reset in rollback.

The socket does not request promiscuous membership, send frames or bind an
uplink. Ownership, route/country/storage safety, firmware/link type and VIF
identity are rechecked approximately every five seconds during the bounded sample;
a failed check ends reception and enters cleanup. Outgoing packets are excluded from reception counts. The parser
validates radiotap length and complete basic 802.11 MAC-header bounds and
reports management/control/data/invalid counts. It does **not** claim full
payload, FCS, handshake or converter validity. Frames live only in bounded
process memory and are discarded. A zero-count test is not reception proof.

### Transport and operator recovery

The approved execution would be streamed from WSL over strict SSH with
`python3 -B`, passing the authenticated `$SSH_CONNECTION` metadata and saving
only sanitized JSON stdout to a private WSL log. Execution emits baseline
and created-VIF ifindex/PHY progress records immediately, before sampling,
so the local record supports targeted console recovery. No script, raw capture or
background rollback unit is written to the Pi. This is the intended command
shape, **not an instruction to execute it before approval**:

```sh
ssh -i ~/.ssh/redux_field_ed25519 -o BatchMode=yes \
  -o StrictHostKeyChecking=yes -o ConnectTimeout=8 \
  -o ServerAliveInterval=10 -o ServerAliveCountMax=3 pi@<verified-Pi-IP> \
  'sudo -n python3 -B - --execute-approved --vif rxv5e16a901 "$SSH_CONNECTION"' \
  < scripts/native-vif-isolation.py
```

SIGHUP/SIGTERM/SIGINT and the main alarm enter the same cleanup path. This
cannot protect against SIGKILL, power loss or every driver failure. **Keep
local Pi console access available during execution.** Do not rely solely on
a Python finally block. If SSH is lost, do not start a second trial: allow
the existing bounded session to finish, reconnect read-only and inspect the
actual state. If it is still running, wait; if restoration failed, report it.

At the local console, the operator can first use read-only `iw dev`,
`ip link show`, `nmcli -g GENERAL.NM-MANAGED,GENERAL.STATE device show wlan0`,
`nexutil -I wlan0 -m`, `iw reg get`, and service state. Only after confirming
the named VIF was created by this trial and retains its recorded PHY/ifindex,
the **same** targeted down/delete commands above are the recovery steps.
Restore managed=yes only after VIF absence/original firmware/interface state
are established; start only redux-live after ownership/country/storage are
safe. If that proof is unavailable or a command failed, stop and request a
new decision. No forced deletion, manager restart, reload, reboot or reflash
is part of this fallback.

### Offline qualification and pending decision

The real state machine is exercised with synthetic process/kernel backends:
normal success; command failures before and after partial side effects;
unsafe baseline; supplicant binding; temporary-VIF manager claims; changed
ifindex; route/regulatory drift; cleanup failure; session interruption; and
changed primary flags after NM restoration. Receive-only socket fixtures
verify outgoing exclusion, deadline limiting, and no transmit API. Header
fixtures cover truncated packets and both raw-802.11/radiotap forms.
Full local suite before concurrent TFT integration: **1,620 passed, 2 skipped**;
integrated workbench: **1,642 passed, 2 skipped**. These are software tests, not
hardware evidence that NM will isolate cleanly or the VIF will receive frames.

**Approval requested:** one supervised attempt that stops only redux-live,
sets only wlan0's runtime NM managed flag to no, creates/activates one
rxv5e16a901 monitor VIF, samples at most 60 seconds passively, then performs
only the gated down/delete/NM-flag/service restoration above. Keep the local
console available. Global managers, persistent network settings, eth0,
country, drivers, power and SD contents remain outside that authorization.
