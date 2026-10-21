# Reviewed native-monitor isolation attempt — 2026-10-16

**Trial outcome: STOPPED before VIF creation. Baseline was subsequently
RESTORED under separate operator approval (see final section).** No receive
sample ran. Do not repeat the trial or infer RF reception.

The operator approved exactly one attempt with local console recovery
available and explicitly required reviewed runner `2b540ad` or a verified
successor, never the earlier `77c833f`. The clean shared workbench was pulled
to `2b540ad745fb826a6c5b266353894e121c99ddd2`. Its CI passed and all 28
then-current isolation tests passed locally. An immutable copy of that exact
runner was saved privately on WSL; concurrent source edits could not change
the executed input. Default read-only preflight passed immediately before
execution, which independently repeats its own preflight.

## Complete sanitized phase report

| Phase / gate | Result | Measured evidence |
|---|---|---|
| BEFORE: identity and transport | PASS | Verified strict SSH via eth0; signed 4f907ff image, Pi 4, 6.12.109 kernel and expected patched module |
| BEFORE: service/radio baseline | PASS | redux-live active with its Bettercap child; only wlan0 on phy0, ifindex 3, monitor mode, UP, ARPHRD 1, firmware mode 0; fresh runtime, Doctor degraded |
| BEFORE: ownership | PASS for candidate isolation | NM managed=true/state20; supplicant unbound; current static safety prerequisites satisfied |
| BEFORE: country/storage | PASS | Configured/global US, unblocked Wi-Fi, healthy REDUXCAP and Ethernet route |
| ISOLATION: stop only redux-live | PASS | Service became inactive; its Bettercap child released the radio |
| ISOLATION: per-device runtime managed=no | PASS for flag, FAIL for safety invariants | NM became managed=false/state10, but primary wlan0 became managed mode and DOWN |
| TEST: native VIF creation/activation | UNKNOWN / NOT ATTEMPTED | Runner stopped at runtime isolation verification before its sole interface-add command |
| TEST: radiotap firmware and decoded reception | UNKNOWN / NOT ATTEMPTED | No temporary interface, packet socket sample or capture artifact |
| AFTER: temporary interface cleanup | PASS / NOT NEEDED | Only original wlan0 remains; ifindex 3, type 1, firmware mode 0 |
| AFTER: original radio/manager/service baseline | FAIL | wlan0 is managed mode/DOWN, NM managed=false/state10, redux-live inactive; restored=false |
| AFTER: surrounding system | PASS | Strict Ethernet SSH still routes via eth0; US unchanged, RF unblocked, storage healthy; redux.service, ingest timer, NM and supplicant remain active; supplicant still unbound |
| AFTER: bounded kernel error sample | PASS for sample only | No matching busy -16, driver error, firmware crash or Oops/panic patterns in the sampled last 80 kernel lines |

Exactly these two mutating commands ran, each once:

```text
systemctl stop redux-live.service
nmcli --wait 5 device set wlan0 managed no
```

The reviewed runner then reported `runtime isolation not confirmed`. The
mode change made its `safe` predicate false, because that predicate requires
the original primary interface to remain in monitor mode. Its cleanup gate
reported `radio baseline not restored; leave manager/service unchanged`.
It intentionally did not re-enable NM or start live under that uncertain
radio state. No later Pi mutation or recovery retry was performed.
The runner process exited; exit status 0 alone is not success: its explicit
`restored=false` and stopped-test fields determine this outcome.

Sanitized baseline/trial stdout and independent after-state records are
preserved in the ignored private WSL directory
`build/native-vif-approved-2b540ad/`. No scripts/logs/captures were installed
on the Pi. No driver reload, global manager restart, country change, permanent
network edit, reboot, reflash, extra image build or repeat experiment occurred.

## Source finding and regression

On this actual Pi, changing the primary device's NM managed flag also changed
its kernel interface mode and UP state. Our offline fake backend had modeled
only the managed flag/state and missed that side effect. It must not be
assumed to be a flag-only transition on future trials.

A source-only regression now reproduces the actual mode/down transition and
requires stopping before VIF creation, preserving the existing cleanup guard
and reporting restoration failure. Source reporting now includes primary
mode plus isolated-phase and cleanup-observed snapshots, so this cause is
visible without relying on a second private diagnostic script. No safety
gates or hardware behavior were loosened, and the new source was not run as
a second experiment or deployed to the Pi.

## Recovery proposal — NEW APPROVAL REQUIRED

The current test authorization did not include directly relabeling or raising
the primary wlan0. The agent therefore stopped rather than add those commands.
A minimal separately approved baseline recovery would:

1. Revalidate Ethernet, US, storage, exact original wlan0/driver, absence of
   any temporary VIF, NM unmanaged, supplicant unbound and no new kernel error.
2. On the already-down, isolated primary only, run once:

       iw dev wlan0 set type monitor
       ip link set dev wlan0 up

3. Verify original monitor/UP, ARPHRD 1, firmware mode 0, US and Ethernet;
   then restore only the prior runtime flag with:

       nmcli --wait 5 device set wlan0 managed yes

4. Verify original managed=true/state20, unchanged primary mode/UP and still
   unbound supplicant. Only then run once:

       systemctl start redux-live.service

5. Require a fresh running checkpoint, no current engine error, expected
   degraded RF Doctor finding and original Ethernet/country/storage state.
   Stop on any failed gate; no driver reload, reboot, forced change or retry.

These are proposed operations only. They were NOT executed. Recovery restores
service baseline, not native radiotap/RF acceptance. A revised native-VIF
experiment is a later separate decision, not bundled into this recovery.

## Separately approved baseline recovery — completed

After the incomplete-restoration report, the operator explicitly approved
baseline recovery only, with local console available. A distinct, bounded
recovery script rechecked the actual stopped state, then ran exactly once:

```text
iw dev wlan0 set type monitor
ip link set dev wlan0 up
nmcli --wait 5 device set wlan0 managed yes
systemctl start redux-live.service
```

Each next command was gated on fresh checks of Ethernet, country, storage,
module identity, original interface identity, absence of a VIF, unbound
supplicant and prior step's measured result. No trial was repeated.

| Recovery verification | Result | Evidence |
|---|---|---|
| Primary radio baseline | PASS | Only wlan0, original ifindex 3, monitor mode/UP, ARPHRD 1, firmware monitor 0 |
| Manager baseline | PASS | NM managed=true/state20, no supplicant binding |
| Ethernet/country/storage | PASS | Original eth0 route/address identity preserved, US unchanged, REDUXCAP identity/rw/free-space checks pass |
| Service/runtime baseline | PASS | redux-live active with Bettercap; new fresh running checkpoint, failure streak 0, no current engine error |
| Original Doctor health | PASS for restoration | Original degraded RF status restored; native RF is not qualified |
| No experimental interface | PASS | No VIF created during the failed trial or this recovery |

Independent final summary at **2026-10-16T17:05:37Z** confirms all three
Redux units active, global/configured US, unblocked Wi-Fi, REDUXCAP mounted,
Bettercap unauthenticated loopback HTTP 401, 2.6-second-old running
checkpoint, engine_attempts 1/events 3, zero failure streak/errors, and fresh
empty worker scans. Both capture inventories remain empty, with zero
handshakes/handoffs/artifacts. These new-process counters do not prove RF.

Sanitized recovery progress/final output is retained privately alongside the
trial log in WSL. **The Pi is no longer left stopped/unmanaged.** No driver
reload, global manager restart, permanent edit, reboot, reflash, packet file
or further native-VIF attempt occurred. Further RF testing remains a separate
review/approval gate, informed by NM's demonstrated primary-mode side effect.
