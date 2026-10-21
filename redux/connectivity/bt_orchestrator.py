"""Bluetooth adapter orchestration — the Wi-Fi orchestrator's idea, for BT.

The device is complete on the onboard Bluetooth controller alone (it carries the
phone PAN management link). Plug in a BT dongle and it's detected and used to add
capability: the onboard keeps the phone PAN, and the dongle becomes a *dedicated*
passive BLE scan radio (feeding the ble_tracker) running side by side — instead
of one controller time-sharing both jobs, or a second controller sitting idle.

Pure decision engine — no DBus/OS calls — so it unit-tests with no hardware. The
live layer enumerates BlueZ adapters (GetManagedObjects) and points the PAN
executor at the management adapter / a BLE scan source at the scan adapter; it
wires onto `plan_bluetooth_roles()`. Every decision carries a reason (glass-box).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, List, Optional

_HCI = re.compile(r"/org/bluez/hci(\d+)\Z")


class BtRole(str, Enum):
    PAN_MANAGEMENT = "pan_management"   # the phone management link (onboard preferred)
    BLE_SCAN = "ble_scan"               # dedicated passive BLE recon (ble_tracker)
    IDLE = "idle"


@dataclass(frozen=True)
class BtAdapter:
    path: str                 # /org/bluez/hciN
    address: str = ""
    onboard: bool = False     # the built-in controller
    powered: bool = True
    le_capable: bool = True   # supports BLE (LE) scanning


@dataclass
class BtAssignment:
    roles: dict                                     # path -> BtRole
    reasons: dict = field(default_factory=dict)     # path -> why (glass-box)
    warnings: list = field(default_factory=list)


def _hci_index(path: str) -> int:
    m = _HCI.match(path or "")
    return int(m.group(1)) if m else 9999


def parse_adapters(managed_objects) -> List[BtAdapter]:
    """Pure parser of a BlueZ GetManagedObjects mapping into BtAdapter records.

    The live layer passes the dict from
    org.freedesktop.DBus.ObjectManager.GetManagedObjects; here we only read the
    org.bluez.Adapter1 interfaces. Onboard = the lowest hci index (hci0), which
    is the built-in controller on a Pi.
    """
    adapters: List[BtAdapter] = []
    if not isinstance(managed_objects, dict):
        return adapters
    for path, ifaces in managed_objects.items():
        if not isinstance(path, str) or not _HCI.match(path):
            continue
        a = (ifaces or {}).get("org.bluez.Adapter1") if isinstance(ifaces, dict) else None
        if not isinstance(a, dict):
            continue
        adapters.append(BtAdapter(
            path=path,
            address=str(a.get("Address", "")),
            powered=bool(a.get("Powered", True)),
            le_capable=True,
            onboard=False,   # set below, by lowest index
        ))
    adapters.sort(key=lambda r: _hci_index(r.path))
    if adapters:
        first = adapters[0]
        adapters[0] = BtAdapter(first.path, first.address, onboard=True,
                                powered=first.powered, le_capable=first.le_capable)
    return adapters


def plan_bluetooth_roles(adapters: Iterable[BtAdapter], *,
                         want_ble_scan: bool = True) -> BtAssignment:
    """Assign BT roles. Onboard carries the phone PAN; a second (dongle) becomes a
    dedicated BLE scan radio so recon runs alongside the management link.
    """
    adapters = sorted(adapters, key=lambda r: (not r.onboard, _hci_index(r.path)))
    roles = {a.path: BtRole.IDLE for a in adapters}
    reasons: dict = {}
    warnings: list = []

    powered = [a for a in adapters if a.powered]
    for a in adapters:
        if not a.powered:
            reasons[a.path] = "idle: adapter is powered off"
    if not powered:
        warnings.append("no powered Bluetooth adapter present")
        return BtAssignment(roles, reasons, warnings)

    # Management link rides the onboard controller when present (stable, built-in).
    mgmt = next((a for a in powered if a.onboard), powered[0])
    roles[mgmt.path] = BtRole.PAN_MANAGEMENT
    reasons[mgmt.path] = ("pan: phone management link on the "
                          + ("onboard controller" if mgmt.onboard else "only adapter"))

    others = [a for a in powered if a.path != mgmt.path]
    scan_placed = False
    for a in others:
        if want_ble_scan and not scan_placed and a.le_capable:
            roles[a.path] = BtRole.BLE_SCAN
            scan_placed = True
            reasons[a.path] = ("ble-scan: dedicated passive BLE recon (ble_tracker) "
                               "running alongside the phone PAN")
        else:
            reasons[a.path] = "idle: spare Bluetooth adapter"

    if want_ble_scan and not scan_placed:
        # Only the management controller is available — BLE recon has to time-share
        # it; a dongle would give it a radio of its own.
        warnings.append(
            f"{mgmt.path}: BLE recon time-shares the management controller — add a "
            f"BT dongle for a dedicated, always-on BLE scan radio")
    return BtAssignment(roles, reasons, warnings)


def select_pan_adapter(assignment: BtAssignment, default: str = "/org/bluez/hci0") -> str:
    """The adapter path to point the PAN executor at (glass-box selection)."""
    for path, role in assignment.roles.items():
        if role is BtRole.PAN_MANAGEMENT:
            return path
    return default


def select_ble_scan_adapter(assignment: BtAssignment) -> Optional[str]:
    """The dedicated BLE scan adapter path, or None if BLE recon time-shares PAN."""
    for path, role in assignment.roles.items():
        if role is BtRole.BLE_SCAN:
            return path
    return None
