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
