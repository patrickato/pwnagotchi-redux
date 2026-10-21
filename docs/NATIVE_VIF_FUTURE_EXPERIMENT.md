# Future native-VIF ownership experiment — consolidated operator procedure

**Status: prepared source procedure, not authorization. Do not execute without
one new approval naming this DOWN-only experiment and its rollback.** It does
not activate a radio, open a packet socket, sample reception, transmit RF,
reload a driver, restart NetworkManager, reboot, deploy software or edit
persistent policy.

## Decision and purpose

NetworkManager 1.42.4 can ignore an interface first observed in nl80211
monitor mode at its Wi-Fi factory. Such a kernel netdev can therefore exist
with ARPHRD 803 while no NMDevice appears in `nmcli`, `GetDevices()` or
`GetAllDevices()`. That explains the October 17 observation but does **not**
prove that NetworkManager is affirmatively excluded from the interface.
Kernel/sysfs existence has no manager-ownership contract; D-Bus absence is
absence of an NM object; `NM_UNMANAGED=true` is an overridable policy hint;
and `ID_NET_MANAGED_BY` is not implemented by the pinned 1.42.4 path.

There is no trustworthy read-only ownership proof for the already-deleted
VIF. The only positive read-only result accepted by current source during a
future simultaneous observation is one stable NM object independently exposed
by both device inventories with `Managed=false` and `State=10`. Stable absence,
udev policy alone, logs, process absence and unchanged link state remain
UNKNOWN. Even explicit unmanaged evidence only informs a later review; the
classifier always returns `permits_activation=false`.

The next recommended physical step is therefore **one DOWN-only observation**,
not activation or reception. Its purpose is to test the source-backed factory
hypothesis and collect a stable, sanitized ownership record.

## Approval scope and exact invocation

Approval must name: one execution of the current reviewed
`scripts/native-vif-isolation.py --execute-approved --down-only`, using only
temporary name `rxv5e16a901`, plus the cleanup below. From the synchronized WSL
checkout, after verifying the host key and current commit, the operator would
run exactly one streamed invocation:

```sh
ssh -i ~/.ssh/redux_field_ed25519 \
  -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 \
  pi@<verified-Pi-address> \
  'sudo -n python3 -B - "$SSH_CONNECTION" --execute-approved --down-only' \
  < scripts/native-vif-isolation.py
```

Do not add `--execute-approved` before that scoped approval. Do not retry a
nonzero result. Keep a local console available throughout. The streamed source
is not an installation and must match the reviewed workbench commit recorded
for the approval.

## Mandatory preconditions

The runner must remeasure all of these in one bounded preflight; any mismatch
stops before mutation:

- Strict Ethernet return route and stable `eth0` identity; console available.
- Exact signed Pi 4 image revision, `6.12.109+rpt-rpi-v8`, installed/loaded
  brcmfmac identity and expected module digest.
- Global/configured US country, unblocked radio, REDUXCAP ext4 identity and
  more than 64 MiB free. This country is valid only at the approved location.
- Exactly original `wlan0`: stable ifindex/phy0, monitor/UP, ARPHRD 1,
  firmware monitor0; no temporary VIF or unexpected wireless interface.
- NetworkManager and wpa_supplicant are the single expected systemd-owned
  processes; supplicant has no bound interface; no foreign capture process.
- `redux-live` is active with a fresh running checkpoint, no current error or
  failure streak, and the previously recorded degraded RF health.
- Kernel log has no current panic, trap, brcmfmac error or busy signature.

## One bounded operation

The reviewed runner performs only this sequence:

1. Acquire its abstract-socket singleton guard and save the complete measured
   baseline in memory.
2. Stop `redux-live.service` once. Require its Bettercap child to disappear
   while NetworkManager still reports original wlan0 managed=yes/state20.
3. Set only wlan0 runtime `managed no` once. Require the already observed
   side effect exactly: wlan0 remains the same ifindex/PHY but becomes
   managed-mode/DOWN, NM managed=no/state10, firmware0, with no new owner.
4. Create exactly one `rxv5e16a901` monitor VIF on verified phy0. Never bring
   it UP. Require a stable positive ifindex, matching PHY, monitor label,
   ARPHRD 801/803, DOWN and firmware0.
5. For at most ten seconds, take two sanitized snapshots. Each pins the unique
   NetworkManager D-Bus owner around `GetDevices()` and `GetAllDevices()`,
   reads only matching Device `Interface`/`Managed`/`State`, and reduces udev
   output to `NM_UNMANAGED`/`ID_NET_MANAGED_BY` classifications. No profile,
   MAC, SSID, address, packet or arbitrary udev value enters the report.
6. Classify the saved report offline with
   `redux.radio.nm_ownership.assess_down_only_samples`. Missing enumeration
   remains `not_enumerated_unknown`; changing owners, inventories, identities
   or properties are inconsistent/unknown. This run never proceeds to RF.

## Immediate stop conditions

Stop the experiment and enter only the bounded cleanup if any precondition
changes; the service or another capture owner returns; wlan0 does not make the
exact measured managed/DOWN transition; the temporary name already exists;
ifindex/PHY/mode/ARPHRD/firmware changes; NetworkManager's unique owner changes;
the two D-Bus inventories disagree; a matching object reports managed=yes;
udev data is malformed/changes; Ethernet, country, storage, process ownership
or kernel health changes; a command exceeds its deadline; SSH is interrupted;
or any unexpected interface appears. Never weaken a failed predicate to finish
the test.

## Bounded rollback and verification

Cleanup is part of the approval but is not permission for retries or expanded
recovery:

1. If and only if the temporary name still has the pinned ifindex/PHY/monitor
   identity, set it DOWN once and delete it once. If identity is uncertain,
   refuse deletion and stop with restoration unverified.
2. Require the original-only interface set, firmware0, original wlan0 ifindex,
   unbound supplicant, Ethernet/country/storage safety and no capture owner.
3. While wlan0 is still NM-unmanaged, restore its original monitor type once
   if needed, verify it, then bring original wlan0 UP once if needed and verify
   again. No driver reload or forced deletion.
4. Restore only wlan0 runtime `managed yes` once. Require original
   managed=yes/state20, monitor/UP, ARPHRD1, firmware0 and unchanged identity.
5. Start only the previously active `redux-live.service` once. Require a new
   fresh running checkpoint, no failure/error, the original Doctor health and
   the unchanged Ethernet/country/storage baseline.

Exit `0` means the DOWN-only observation completed and the original baseline
was verified restored; it is not RF success. Exit `2` means the experiment
stopped but restoration verified. Exit `3` means restoration could not be
verified and needs operator review at the console. Never automatically retry.

## What remains after a successful DOWN-only run

No result from this procedure proves firmware monitor2, decoded reception,
closed PCAP rotation, handoff or conversion. If the VIF again has no NM object,
ownership remains blocked. A future exact-interface ownership policy, NM
service isolation, or activation test would be a different physical change
requiring source review, rollback qualification and new approval.

Primary references: NetworkManager 1.42.4
[Wi-Fi factory](https://github.com/NetworkManager/NetworkManager/blob/1.42.4/src/core/devices/wifi/nm-wifi-factory.c),
[nl80211 mode mapping](https://github.com/NetworkManager/NetworkManager/blob/1.42.4/src/libnm-platform/wifi/nm-wifi-utils-nl80211.c),
[manager device enumeration](https://github.com/NetworkManager/NetworkManager/blob/1.42.4/src/core/nm-manager.c), and
[D-Bus API](https://networkmanager.dev/docs/api/1.42.4/gdbus-org.freedesktop.NetworkManager.html).
