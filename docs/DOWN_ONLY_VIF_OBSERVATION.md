# Prepared DOWN-only native VIF ownership observation

**Preparation only. NOT AUTHORIZED; no experiment executed.** Keep this
separate from split-radio/phone development. Pinned NM 1.42.4 factory and
udev findings remain in [NATIVE_VIF_NM_EVIDENCE.md](NATIVE_VIF_NM_EVIDENCE.md).
Missing nmcli/GetAllDevices enumeration is never managed=false.

## One proposed attempt and exact bounds

Use the console-verified ED25519 key over strict Ethernet SSH. Local console
must be available. Recheck exact signed Pi 4 image/kernel/module, configured
and global US, RF unblock, dedicated ext4 capture identity/free space,
original wlan0 ifindex/PHY, monitor/UP/type1/firmware0, NM yes/state20, unbound
supplicant and fresh running Redux. Require absence of rxv5e16a901 and every
unexpected interface/process. Abort before mutation on any failed gate.

1. `systemctl stop redux-live.service`; observe inactive, no Bettercap and
   unchanged independent safety baseline.
2. `nmcli --wait 5 device set wlan0 managed no`; require explicit no/state10,
   measured managed/DOWN/type1/firmware0 and stable primary identity.
3. Once only: `iw phy phy0 interface add rxv5e16a901 type monitor`.
   Immediately retain the bounded post-create snapshot, ifindex/PHY/mode,
   actual sysfs ARPHRD and flags, firmware readback and NM evidence.
   **Never issue a VIF-UP command or open a packet socket.** Unexpected UP,
   firmware mode or identity terminates observation and enters safe cleanup.
4. For no more than ten seconds wall-clock (individual commands capped at
   two seconds), read NM GetDevices/GetAllDevices using busctl with
   `--auto-start=no`; for matching objects read Interface, Managed and State.
   Read only NM_UNMANAGED and ID_NET_MANAGED_BY from that VIF's existing udev
   database. Recheck stable ifindex/PHY, DOWN flag, firmware0 and independent
   safety. No udev test/trigger/reload, policy injection, NM flag changes on
   the VIF or discovery scan. Publish only fixed predicates, booleans, enums,
   counts and ifindex; no raw udev, D-Bus paths, connection names or addresses.
   Deadline or command failure stops observation; no automatic retry.

## Only planned rollback

- If the created VIF still has its recorded monitor/PHY/ifindex identity,
  attempt `ip link set dev rxv5e16a901 down` then
  `iw dev rxv5e16a901 del`, once each. Uncertain/replaced identity refuses
  deletion and reports restoration unverified. Do not remove another netdev.
- Require original interface inventory, primary identity/type and firmware0,
  no capture owner or supplicant binding, stable Ethernet/US/storage/driver.
- While NM remains explicitly no/state10, restore original primary only as
  needed: `iw dev wlan0 set type monitor`, verify, then
  `ip link set dev wlan0 up`, verify. No guessed mode/ownership transitions.
- After original monitor/UP and all invariants pass:
  `nmcli --wait 5 device set wlan0 managed yes`; verify original state20 and
  unchanged kernel mode/UP, type, firmware and identity.
- Start only previously active `redux-live.service`; independently verify
  fresh running runtime, Bettercap and original degraded Doctor baseline.
  Any failed gate stops remaining mutations, preserves sanitized state and
  requires a new recovery decision. No retry, module reload or power action.

Use the reviewed runner's 210-second main/90-second cleanup limits and
single-trial lock model, with explicit intent flags for partial commands.
**The existing runner cannot execute this variant:** it contains an UP and
sample stage. Before requesting approval, implement and offline-review a
separate DOWN-only state machine or explicitly bounded mode with regression
coverage proving no UP/sample path, deadline cleanup and identity refusal.
This document is a reviewable command plan, not a ready execution launcher.

The observation can establish actual per-VIF DBus/udev facts and test the
source hypothesis. It cannot qualify RF reception or waive unknown ownership.
No package/service/configuration changes, persistent artifacts on the Pi,
raw capture, RF activation or policy workaround belong to this procedure.
