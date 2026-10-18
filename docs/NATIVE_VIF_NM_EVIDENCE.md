# Native radiotap VIF: NetworkManager ownership evidence (source-only)

**Status: investigation only. No new Pi experiment authorized.**

## Observed Oct 17 blocker

The owned, disposable Pi 4 successfully completed one named kernel
`iw phy ... interface add ... type monitor` operation: VIF ifindex 5,
phy0, monitor-labelled **ARPHRD 803**, firmware0 before activation.
The primary was already isolated NM no/state10 in managed/DOWN. However,
`nmcli device status` did **not** enumerate the newly created VIF, so
`temp_nm_managed=null`. The post-create gate correctly stopped.
No VIF-UP, firmware2 transition, received 802.11 frame, PCAP or handoff
was demonstrated. Fully verified VIF deletion and original-state recovery
were successful. See [NATIVE_VIF_TRIAL_02080F0_20261017.md](NATIVE_VIF_TRIAL_02080F0_20261017.md).

**Do not conflate three distinct propositions:**
1. A netdevice exists and is monitor-labelled with ARPHRD 803
   (kernel identity and framing).
2. NetworkManager has (or has not) discovered a device object for it.
3. NetworkManager is affirmatively configured not to manage/alter it.

(1) is physically demonstrated. (2) was negative in `nmcli` at one
instant. **That is not (3)**. Full RF operation is still unverified.

## Research-backed alternate evidence to investigate

NetworkManager exposes D-Bus `GetDevices()` (realized devices) and
`GetAllDevices()` (also includes placeholders). When it exposes a
matching D-Bus device, its `Managed` property is better evidence than
assuming device-list absence. Enumerating only `nmcli device status`
does not rule out a not-yet-realized D-Bus entry. In particular,
`GetDeviceByIpIface` may not be useful for a non-IP monitor device.

Newer NetworkManager versions document an additional udev policy; **this
is not implemented in upstream 1.42.4** (see pinned-source review below):
`ID_NET_MANAGED_BY` **when explicitly set to a value different from
`org.freedesktop.NetworkManager`** requests that NM leave that device
unmanaged. `NM_UNMANAGED=true` separately requests unmanaged but can
be overridden by runtime configuration; if `NM_UNMANAGED` is set (including
false), NetworkManager ignores `ID_NET_MANAGED_BY`. The classification helper
now requires independently verified implementation support before interpreting
`ID_NET_MANAGED_BY` as an ownership policy. A missing property is not
an affirmative policy. Never fabricate or inject this property as
a workaround without its own reviewed physical-change approval.

References:
- [NetworkManager D-Bus device enumeration](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/gdbus-org.freedesktop.NetworkManager.html)
- [NetworkManager udev ownership semantics](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/NetworkManager.html)
- [udevadm read-only property query](https://manpages.debian.org/bookworm/udev/udevadm.8.en.html)
- [NetworkManager per-device runtime versus persistent settings](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html)

## Source-only implementation

`redux/radio/nm_ownership.py` now provides a **pure read-only
classification helper**. `tests/test_nm_ownership.py` exercises the
observed unenumerated case, contradictory NM/D-Bus evidence, explicit
udev policies, absent devices and suppression of private udev fields.

All its outcomes intentionally set `permits_activation=False`.
It must not be called as a substitute for the runner's existing safety
gates. An independent review and a new scoped hardware approval are
required before any changed execution sequence can be used.

The shared classifier now also consumes the existing DOWN-only runner's two
sanitized snapshots. It requires stable ifindex/ARPHRD/DOWN/firmware identity,
a stable NetworkManager unique D-Bus owner, agreement between `GetDevices`
and `GetAllDevices`, and stable filtered udev classifications. Only one
matching NM object reporting Managed=false/State10 is positive ownership
evidence; two stable empty inventories still produce
`not_enumerated_unknown`. See
[NATIVE_VIF_FUTURE_EXPERIMENT.md](NATIVE_VIF_FUTURE_EXPERIMENT.md) for the
single consolidated future procedure. It is not authorization.

## Codex's next work: read-only investigation, no new VIF

Using the existing verified strict Ethernet SSH and previously inspected
Pi identity, Codex can inspect without changing anything:

- Confirm installed NM version, `nmcli` device-visible status for
  original `wlan0`, and D-Bus `GetDevices`/`GetAllDevices` support,
  using `busctl --system --auto-start=no` or equivalent read-only
  interface introspection. Distinguish D-Bus enumeration from Managed.
- Check the **existing** original `wlan0` udev property database
  (e.g. `udevadm info --query=property --path=/sys/class/net/wlan0`)
  for only `NM_UNMANAGED` and `ID_NET_MANAGED_BY`, locally filtering
  everything else. Do not publish raw udev properties, addresses, device
  IDs, connection profiles or MAC/SSID values.
- Read only the effective NM configuration/policy relevant to monitor
  devices. Do not reload, edit or restart NetworkManager; do not trigger
  udev events, change the radio or create another VIF.
- Review NM **1.42.4** source behavior for `NL80211_IFTYPE_MONITOR`
  and why `nmcli` may lack a device entry. Make a testable hypothesis,
  not an inference that the temporary VIF was definitely unmanaged.
- Decide whether any alternative affirmative **read-only** evidence
  is available in the installed kernel/udev/NM environment. If not,
  propose a bounded future solution and its exact new approval scope,
  keeping the original fail-closed gate.

Any diagnostic that would require the temporary VIF to exist is a
**future hardware experiment**, not part of the current read-only work.

## Decision after source investigation

If affirmative NM control exclusion can be established with independently
reviewed, bounded observations, propose a new one-attempt runner and explicit
operator approval. If it cannot, consider a **separately approved, isolated
and reversible ownership policy** and offline qualification first—not a
silent waiver of the NM-ownership predicate. Guard against policy leakage
to `eth0` or the permanent system, and account for unsuccessful cleanup.

Activation/firmware2 and decoded receive counts are still **unknown**.
No image rebuild is justified by the current evidence.


## Completed read-only/source investigation — 2026-10-17

**Decision: no affirmative live ownership evidence for the deleted monitor
VIF is available from the current baseline. Activation stays blocked.**
No VIF was created and no NM/udev/service/radio state was changed in this
investigation. The existing runner is unchanged. All classifier outcomes
continue to set `permits_activation=False`.

### Pinned implementation, not current-manual assumptions

Reviewed upstream tag **1.42.4**, using these exact source paths:

- [Wi-Fi factory](https://github.com/NetworkManager/NetworkManager/blob/1.42.4/src/core/devices/wifi/nm-wifi-factory.c#L104):
  `create_device` accepts infrastructure, ad-hoc, AP and mesh modes; for
  monitor/other unsupported modes it sets the ignore result and returns no
  NMDevice. The manager returns on this factory result; it does not fall
  back to a generic device for a deliberately ignored link.
- [nl80211 mode mapping](https://github.com/NetworkManager/NetworkManager/blob/1.42.4/src/libnm-platform/wifi/nm-wifi-utils-nl80211.c#L170):
  monitor is not mapped to one of those supported modes. An interface first
  seen in monitor mode therefore fits the factory's ignore path. This is a
  source-backed explanation of the prior snapshot, **not a measured live
  ownership exclusion**. It does not contradict the already-created wlan0
  NM object remaining visible after a later primary-mode change.
- [Manager enumeration](https://github.com/NetworkManager/NetworkManager/blob/1.42.4/src/core/nm-manager.c#L4246):
  GetDevices and GetAllDevices iterate existing NM device objects; the
  difference is filtering unrealized objects. GetAllDevices does not query
  every kernel netdev or synthesize an object for a factory-ignored VIF.
  Thus switching from nmcli to GetAllDevices alone is unlikely to recover
  a Managed property for this monitor VIF. This remains a hypothesis until
  an approved VIF exists for the simultaneous measurement.
- [Udev policy implementation](https://github.com/NetworkManager/NetworkManager/blob/1.42.4/src/libnm-platform/nm-platform.c#L1618):
  `nm_platform_link_get_unmanaged` checks **NM_UNMANAGED only**, not
  ID_NET_MANAGED_BY. The pinned [manual](https://github.com/NetworkManager/NetworkManager/blob/1.42.4/man/NetworkManager.xml#L193)
  likewise documents NM_UNMANAGED's runtime-overridable behavior.
  A newer manual cannot establish this older daemon's feature support.
- [Default udev rules](https://github.com/NetworkManager/NetworkManager/blob/1.42.4/data/85-nm-unmanaged.rules):
  the rules cover several virtualization/gadget devices, not native monitor
  interfaces. Do not infer an applicable monitor exclusion from their presence.

### Actual Pi observations, all read-only

- Installed package: **1.42.4-1+rpt1+deb12u1**; nmcli reports 1.42.4.
  The installed daemon contains NM_UNMANAGED's key string but not
  ID_NET_MANAGED_BY's. This corroborates the upstream version distinction;
  string inspection alone is not execution-path or ownership proof.
- Both D-Bus methods succeeded and each returned three device objects.
  Among the target names, both exposed only original wlan0, Managed=true,
  State20. There is currently no temporary VIF; no ownership assertion about
  a nonexistent VIF follows from these inventories.
- Original wlan0's existing udev database has neither ownership property.
  No local/runtime ownership-rule entry was found in the inspected rules
  directories. The packaged rule has five active NM_UNMANAGED assignments,
  none monitor/trial-specific, and SHA256
  `afce2a870bf2cefb2b52214ed959d0e0025ed2efef5c05871895f6edbedec24a`,
  exactly matching pinned upstream 85-nm-unmanaged.rules.
- Filtered effective configuration showed no monitor/trial-specific matcher
  or unmanaged-devices exclusion. Other sections exist; unrelated selectors
  and all connection profiles were withheld. This is not proof about every
  possible policy source or a future VIF's computed udev properties.
- The full existing safety observation passed: strict Ethernet route and
  identity, signed image/kernel/module, US, storage, unbound supplicant and
  original wlan0 monitor/UP/type1/firmware0. Redux stayed active and fresh;
  Doctor remains degraded for native RF. No captures or packets were read.

### Source corrections and remaining boundary

The pure classifier now treats ID_NET_MANAGED_BY as unsupported or unverified
unless the caller separately verifies implementation support. Explicit
NM_UNMANAGED=false suppresses fallback to that property. Invalid numeric
state and contradictory managed=yes/D-Bus-absent evidence cannot be reported
as valid live evidence. Invalid caller strings are never echoed into the
allowlisted result. Tests cover these cases. This helper remains diagnostic,
not authorization and not a replacement for the execution gate.

**Safest next experiment, if desired: ownership-only, DOWN-only observation.**
Prepare/review a separate one-attempt procedure that retains the original
preflight, redux-live stop, primary runtime isolation and verified cleanup,
creates only rxv5e16a901 **without bringing it UP**, and observes its stable
ifindex/PHY/monitor/ARPHRD plus GetDevices/GetAllDevices and filtered udev
properties for a bounded discovery window (e.g. ten seconds). No activation,
firmware setter or packet socket. Preserve UNKNOWN when no affirmative NM
exclusion appears, then remove only the identified VIF and restore the
original primary/NM/live baseline through the existing gates. Such a test
would measure the source hypothesis and actual per-VIF udev properties; it
would not itself prove capture readiness. Do not repeat the activation trial
unchanged: the current source predicts the same missing-object gate.

This is a **new physical-change boundary**. No further experiment is
approved. If that observation also finds no affirmative exclusion, a
version-supported, exact-interface, reversible policy would need its own
source review, rollback qualification and explicit authorization. Do not
install an ID_NET_MANAGED_BY rule as a workaround on this 1.42.4 daemon,
change permanent policy, or bypass the gate based on source exclusion alone.

Offline qualification: **1,730 tests passed, 2 skipped**, including the
classifier and unchanged native-VIF runner regressions. No hardware-change
authorization or physical capture success follows from these tests.
