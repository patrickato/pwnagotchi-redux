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

NetworkManager's documented udev policy is another independent source:
`ID_NET_MANAGED_BY` **when explicitly set to a value different from
`org.freedesktop.NetworkManager`** requests that NM leave that device
unmanaged. `NM_UNMANAGED=true` separately requests unmanaged but can
be overridden by runtime configuration; if `NM_UNMANAGED` is set,
NetworkManager ignores `ID_NET_MANAGED_BY`. A missing property is not
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
