# Signed image Pi 4 read-only acceptance — 2026-10-16

The operator verified the new SSH ED25519 fingerprint at the physical Pi
console. The local agent matched a fresh keyscan to that fingerprint, updated
only the obsolete single-host known_hosts entry, and connected with strict
checking. SSH return routing uses Ethernet `eth0`. No Pi radio, services,
drivers, configuration, captures or power state were modified by diagnostics.
No image build was started. Keep the comparison and Jayofelony cards intact.

Primary summary sampled at **2026-10-16T10:57:32Z**; bounded supplemental
observations followed. PASS means the stated measurement passed, not full
system/RF qualification.

| Gate | Result | Evidence |
|---|---|---|
| Verified SSH / Ethernet | PASS | Console fingerprint matched; strict SSH, `eth0` transport |
| Signed-image identity / ARM64 boot | PASS | Baked Redux `4f907ff93ce0c6bc0b2c9d4f68025ba7dd1f9a84`; aarch64; kernel `6.12.109+rpt-rpi-v8` |
| Installed patched driver | PASS | `updates/brcmfmac.ko`, correct vermagic and SHA256 `0ebeaf3767e2210304357fb80bf3386715c4ecddcfd2aeccadde83714078a3ba` |
| Loaded driver corroboration | PASS | brcmfmac present in sysfs; loaded srcversion matches installed file (not a hash of live kernel memory) |
| Firmware file provenance | PASS | Patched firmware digest matches image build record; does not independently prove firmware execution mode |
| Services / runtime | PASS | Three Redux units active; runtime running, zero current failures/error; checkpoint age 5 seconds; live service NRestarts 0 |
| Bettercap / dashboard | PASS | Unauthenticated loopback REST 401; local Redux status/Doctor API responds |
| Country / RF blocks | PASS | Configured/global US; Wi-Fi unblocked |
| Storage | PASS | Exact `/captures` ext4 rw, 472,977,408 bytes available; overlay root with a read-only ext4 lower mount observed |
| Native capture framing / monitor activation | FAIL | Only Wi-Fi netdev `wlan0`; iw monitor, channel 8, but ARPHRD 1 (Ethernet), Nexutil firmware monitor 0 |
| Real decoded 802.11 reception | UNKNOWN | No native packet-reception evidence; runtime events do not prove RF |
| Closed capture handoff | UNKNOWN | Active/incoming inventories empty, no truncation; handshake events 0, handoffs 0 |
| Worker operation | PASS | Available ledger, recent empty scan, no scan error, zero artifacts |
| Actual capture conversion | UNKNOWN | hcxpcapngtool present; no source capture/job to validate conversion |
| Doctor overall acceptance | FAIL | Overall degraded; native RF link finding degraded. Handoff and thermal/power unknown; location/scope attention |
| Bounded kernel-error sample | PASS | Last 200 kernel lines: no matched -16 busy, firmware crash, Oops/panic or MMC error patterns. Not a full-boot audit |
| Hardware throttle probe | PASS | `vcgencmd get_throttled` reports `0x0`; does not close Doctor's unconnected thermal/power telemetry gate |
| 480x320 TFT / touch | UNKNOWN | Only fb0 enumerated, 1920x1080/16bpp/stride 3840; eight input event nodes, none selected or exercised |
| Cold-boot persistence / endurance / Pi 5 | UNKNOWN | Not tested in this session |

The checkpoint recorded engine_attempts 188 and events_seen 564. These are
runtime/service counters, not decoded frame counts. Uptime was about 15.7
hours at the supplemental probe. No new module deployment/reload is needed
to explain the current failure: the expected patched image is running, but
the selected primary interface still lacks the native monitor lifecycle.
The destructor fix addresses VIF teardown, not primary-interface relabeling.

## Next hardware test — proposal only, separate approval required

A single bounded native monitor-VIF lifecycle test on this disposable card
would test the remaining hypothesis without another build or driver reload:

1. Confirm Ethernet SSH and US, take sanitized baseline, stop only redux-live.
2. Create one uniquely named native monitor VIF on the existing radio; do
   not change NetworkManager configuration or any persistent file. Bring
   that temporary interface up and measure ARPHRD and firmware monitor mode.
3. If it exposes radiotap, observe at most 60 seconds of passive reception,
   reporting decoded frame counts/link format only. No active RF commands,
   attacks, persistent captures or upload of identifiers/artifacts.
4. Bring down/delete the created VIF, restart redux-live and verify original
   interface, US, firmware mode, service, Doctor and Ethernet connectivity.
5. Stop on unexpected errors. Never force removal, reload a driver, retry
   the experiment, reboot, reflash or repair configuration automatically.
   If cleanup fails, report the exact sanitized state for a new decision.

This is a reviewable proposal, not an authorization or an executed test.
The earlier native-VIF attempt failed before sampling; do not repeat its
NetworkManager assumption. The implementation must use the actual kernel
interface and reject unexpected state before any physical operation.
TFT overlay/configuration and first pixel write remain separate later gates.

## Independent source improvement

Doctor previously omitted the native-link finding when a selected running
interface had no usable ARPHRD measurement. New source explicitly adds an
UNKNOWN finding/coverage gap for missing, boolean, non-integer and out-of-range
values; valid unexpected integer types still receive ATTENTION. This is a
source-only improvement with synthetic regressions, not a deployed Pi fix.
