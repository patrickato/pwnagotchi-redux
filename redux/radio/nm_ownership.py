"""Read-only evidence about NetworkManager's ownership of a monitor VIF.

This is *diagnostic evidence classification*, not permission to activate RF.
The created radiotap VIF was absent from nmcli on the Pi4 on 2026-10-17;
lack of enumeration is never interpreted as managed=no.

Input strings must be raw **local** udev property output, not published logs.
Only allowlisted outcomes are returned; no SSID/MAC/profile/udev values leak.
NetworkManager's documented ID_NET_MANAGED_BY rule and NM_UNMANAGED override
semantics require separate, explicitly observed device policy.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OwnershipEvidence:
    status: str
    nm_enumerated: bool | None
    dbus_enumerated: bool | None
    udev_policy: str
    reason: str
    # Intentionally never pass this flag to a radio runner as an authorization.
    # A distinct hardware review must decide what evidence is sufficient.
    permits_activation: bool = False


def udev_ownership_policy(properties: str | None, *,
                          supports_id_net_managed_by: bool | None = None) -> str:
    """Parse only two documented udev fields; reject ambiguity and duplicates.

    A udev policy is not proof that a running NetworkManager instance has
    applied it, so callers must still independently inspect NM's live state.
    ID_NET_MANAGED_BY requires separately verified implementation support;
    NetworkManager 1.42.4 does not implement it. Unknown support fails closed.
    """
    if properties is None or not isinstance(properties, str) or len(properties) > 32768:
        return "unknown"
    values: dict[str, str] = {}
    for line in properties.splitlines():
        if not line.startswith(("NM_UNMANAGED=", "ID_NET_MANAGED_BY=")):
            continue
        name, value = line.split("=", 1)
        if name in values or len(value) > 128:
            return "unknown"
        values[name] = value.strip()
    # The NM_UNMANAGED rule explicitly takes precedence over MANAGED_BY.
    unmanaged = values.get("NM_UNMANAGED")
    if unmanaged is not None:
        if unmanaged.lower() in ("1", "true"):
            return "udev_requests_unmanaged_overridable"
        if unmanaged.lower() in ("0", "false", ""):
            # Presence takes precedence even when false; never fall through
            # to ID_NET_MANAGED_BY after an explicit NM_UNMANAGED value.
            return "udev_requests_managed"
        return "unknown"
    controller = values.get("ID_NET_MANAGED_BY")
    if not controller:
        return "not_declared"
    if supports_id_net_managed_by is not True:
        return ("managed_by_unsupported" if supports_id_net_managed_by is False
                else "managed_by_support_unverified")
    if controller == "org.freedesktop.NetworkManager":
        return "names_networkmanager"
    # A different manager label explicitly rules out NM under the documented
    # udev behavior (when NM_UNMANAGED is absent and support is verified).
    return "names_other_manager"


def assess_ownership(*, nm_managed: bool | None, nm_state: int | None,
                     dbus_enumerated: bool | None, udev_properties: str | None,
                     kernel_interface_present: bool,
                     supports_id_net_managed_by: bool | None = None) -> OwnershipEvidence:
    """Classify one read-only observation without inferring negative ownership.

    D-Bus enumeration alone means NM knows a device; it is *not* its Managed
    property. Missing both nmcli and D-Bus never proves unmanaged.
    """
    policy = udev_ownership_policy(
        udev_properties, supports_id_net_managed_by=supports_id_net_managed_by)
    nmlist = nm_managed is not None if type(nm_managed) in (bool, type(None)) else None
    dbus_observed = dbus_enumerated if type(dbus_enumerated) in (bool, type(None)) else None
    def outcome(status: str, reason: str) -> OwnershipEvidence:
        return OwnershipEvidence(status, nmlist, dbus_observed, policy, reason)
    if kernel_interface_present is not True:
        return outcome("no_kernel_interface", "No observed netdevice to classify.")
    if type(nm_managed) not in (bool, type(None)) or type(dbus_enumerated) not in (bool, type(None)):
        return outcome("unknown", "Invalid NM evidence.")
    if nm_state is not None and (type(nm_state) is not int or nm_state < 0):
        return outcome("unknown", "Invalid NM state evidence.")
    if nm_managed is True and dbus_enumerated is False:
        return outcome("inconsistent", "NM managed=yes conflicts with D-Bus enumeration.")
    if nm_managed is True:
        return outcome("nm_managed", "NetworkManager explicitly reports managed=yes.")
    if nm_managed is False:
        if nm_state == 10 and dbus_enumerated is not False:
            return outcome("nm_explicitly_unmanaged",
                           "NM reported managed=no, state10; verify independently before activation.")
        return outcome("inconsistent",
                       "NM managed=no conflicts with state or D-Bus enumeration.")
    # NM has not enumerated the interface via nmcli: don't equate that with
    # a runtime ownership decision even if an external policy might apply.
    if dbus_enumerated is True:
        return outcome("nm_dbus_visible_unknown",
                       "NM D-Bus recognizes device without its Managed property.")
    if dbus_enumerated is None:
        return outcome("unknown", "NM D-Bus discovery has not been measured.")
    if policy == "names_other_manager":
        return outcome("udev_policy_not_nm",
                       "Udev explicitly names another manager; live NM non-claim still needs verification.")
    if policy == "udev_requests_unmanaged_overridable":
        return outcome("udev_unmanaged_overridable",
                       "Udev requests unmanaged, but NM can override it at runtime.")
    return outcome("not_enumerated_unknown",
                   "NM device not enumerated; no affirmative exclusion or unmanaged state.")


def assess_down_only_samples(samples) -> OwnershipEvidence:
    """Classify the runner's two sanitized snapshots, never raw identifiers.

    A positive result requires both NM inventory APIs to expose exactly one
    matching object with the same explicit Managed=false/State=10 values in
    both observations. Absence remains unknown because NM 1.42.4 can ignore a
    monitor-mode link at its Wi-Fi factory without creating an NMDevice.
    """
    def result(status, policy="unknown", reason="Invalid or changing DOWN-only evidence."):
        return OwnershipEvidence(status, None, None, policy, reason)
    if not isinstance(samples, list) or len(samples) != 2:
        return result("unknown")
    normalized=[]
    for item in samples:
        if (not isinstance(item, dict) or item.get("up") is not False
                or item.get("firmware") != 0 or item.get("nm_owner_stable") is not True):
            return result("unknown")
        ifindex=item.get("ifindex")
        arphrd=item.get("arphrd")
        if type(ifindex)is not int or ifindex<=0 or type(arphrd)is not int or arphrd not in (801,803):
            return result("unknown")
        inventories=item.get("inventories")
        udev=item.get("udev")
        if not isinstance(inventories,dict)or set(inventories)!={"GetDevices","GetAllDevices"}or not isinstance(udev,dict):
            return result("unknown")
        observations=[]
        for method in ("GetDevices","GetAllDevices"):
            inventory=inventories[method]
            if not isinstance(inventory,dict)or set(inventory)!={"device_count","matches"}:
                return result("unknown")
            count=inventory["device_count"];matches=inventory["matches"]
            if type(count)is not int or not 0<=count<=64 or not isinstance(matches,list)or len(matches)>1:
                return result("inconsistent")
            if not matches:
                observations.append(None);continue
            match=matches[0]
            if (not isinstance(match,dict)or set(match)!={"managed","state"}
                    or type(match.get("managed"))is not bool
                    or type(match.get("state"))is not int or not 0<=match["state"]<=120):
                return result("unknown")
            observations.append((match["managed"],match["state"]))
        if observations[0]!=observations[1]:
            return result("inconsistent")
        unmanaged=udev.get("NM_UNMANAGED")
        controller=udev.get("ID_NET_MANAGED_BY")
        allowed={"absent","true","false","present_uninterpreted"}
        if unmanaged not in allowed or controller not in allowed:
            return result("unknown")
        normalized.append((ifindex,arphrd,observations[0],unmanaged,controller))
    if normalized[0]!=normalized[1]:
        return result("inconsistent")
    _,_,observation,unmanaged,controller=normalized[0]
    properties=("NM_UNMANAGED="+("true"if unmanaged=="true"else"false")+"\n"
                if unmanaged in ("true","false")else"")
    # ID_NET_MANAGED_BY is intentionally uninterpreted on NM 1.42.4.
    if observation is None:
        return assess_ownership(nm_managed=None,nm_state=None,dbus_enumerated=False,
            udev_properties=properties,kernel_interface_present=True,
            supports_id_net_managed_by=False)
    managed,state=observation
    return assess_ownership(nm_managed=managed,nm_state=state,dbus_enumerated=True,
        udev_properties=properties,kernel_interface_present=True,
        supports_id_net_managed_by=False)
