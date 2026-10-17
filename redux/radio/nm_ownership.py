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


def udev_ownership_policy(properties: str | None) -> str:
    """Parse only two documented udev fields; reject ambiguity and duplicates.

    A udev policy is not proof that a running NetworkManager instance has
    applied it, so callers must still independently inspect NM's live state.
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
        if unmanaged.lower() not in ("0", "false", ""):
            return "unknown"
    controller = values.get("ID_NET_MANAGED_BY")
    if not controller:
        return "not_declared"
    if controller == "org.freedesktop.NetworkManager":
        return "names_networkmanager"
    # A different manager label explicitly rules out NM under the documented
    # udev behavior (when NM_UNMANAGED is not asserted).
    return "names_other_manager"


def assess_ownership(*, nm_managed: bool | None, nm_state: int | None,
                     dbus_enumerated: bool | None, udev_properties: str | None,
                     kernel_interface_present: bool) -> OwnershipEvidence:
    """Classify one read-only observation without inferring negative ownership.

    D-Bus enumeration alone means NM knows a device; it is *not* its Managed
    property. Missing both nmcli and D-Bus never proves unmanaged.
    """
    policy = udev_ownership_policy(udev_properties)
    nmlist = nm_managed is not None
    def outcome(status: str, reason: str) -> OwnershipEvidence:
        return OwnershipEvidence(status, nmlist, dbus_enumerated, policy, reason)
    if kernel_interface_present is not True:
        return outcome("no_kernel_interface", "No observed netdevice to classify.")
    if type(nm_managed) not in (bool, type(None)) or type(dbus_enumerated) not in (bool, type(None)):
        return outcome("unknown", "Invalid NM evidence.")
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
