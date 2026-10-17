# One approved f4f83c0 native-monitor trial — 2026-10-17

**STOPPED before activation; restoration fully verified. Approval consumed.**
No repeat, driver reload, reboot, reflash, permanent network change or global
manager restart was performed. The original Jayofelony card is untouched.

The owner approved exactly one supervised attempt using reviewed `f4f83c0`,
with local console available. The workbench was clean and pulled up to date.
The exact frozen runner matched that commit and the working source:
SHA256 `89c836a033b35c8d21e891c86b2d034159ee447c656bffa00e6abdb6c83d94d7`.
All 60 hardware-free runner tests passed before execution. Strict SSH used
only the previously console-verified ED25519 host identity. No host-key
warning was bypassed. Logs remain private under the ignored local build tree.

| Phase | Gate | Measured evidence |
|---|---|---|
| BEFORE | PASS | Fresh read-only preflight and execution baseline: strict Ethernet route/identity, pinned Pi 4 signed image/kernel/module, US, unblocked RF, healthy dedicated storage and unbound supplicant; only original wlan0 ifindex3 on phy0, monitor/UP, ARPHRD1, firmware0; NM managed/state20; fresh active Redux |
| ISOLATION | PASS | redux-live inactive and Bettercap absent; wlan0 NM unmanaged/state10, expected kernel managed/DOWN, unchanged ifindex3/phy0/type1, firmware0, all independent safety checks passed |
| TEST | FAIL | One create command returned successfully; the subsequent gate stopped with `temporary interface isolation not confirmed`. No activation or sampling command followed |
| Native link/firmware | UNKNOWN | Temporary ARPHRD, ownership, identity and firmware observations were not retained by this revision before its combined gate failed. Cannot determine which predicate failed from this report |
| Reception | UNKNOWN / NOT SAMPLED | No passive socket sample ran; decoded reception counts are unavailable, not a measured zero |
| AFTER | PASS | Cleanup reported no errors and restored=true; original interface set, wlan0 ifindex3/phy0 monitor/UP/type1/firmware0, NM managed/state20, empty supplicant bindings, safety conditions and fresh running Redux with original degraded Doctor health verified |

The planned cleanup observed only original wlan0 before restoring its
monitor/UP state, original NM ownership and Redux. No temporary VIF remains.
The process returned **2**, correctly distinguishing a stopped trial with
verified restoration from a completed experiment or failed restoration.
No unapproved recovery operation was attempted.

Independent read-only summary at **2026-10-17T13:03:35Z** reconfirmed all
three Redux units active, Bettercap HTTP401, global/configured US, unblocked
Wi-Fi, dedicated ext4 captures and a 2.3-second fresh running checkpoint
with zero failures/current errors. Active/incoming captures, processing
artifacts and handoffs remain empty. wlan0 remains monitor-labelled but
Ethernet ARPHRD1. Runtime event counters are not RF evidence.

Next safe work is source-only observability: retain an allowlisted temporary
VIF snapshot before each failed gate so identity, NM ownership, ARPHRD and
firmware failures can be distinguished. Do not infer that NM claimed the
VIF or weaken any safety predicate from this combined error. A further
hardware attempt would require independent source review and new approval.
