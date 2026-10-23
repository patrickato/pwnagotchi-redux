"""SDR device orchestration — split dongles across source roles.

The ingest layer already understands two SDR sources (rtl_433 ISM and dump1090
ADS-B), but nothing decided which physical dongle runs which. One RTL-SDR can't
run both at once, so with a single dongle you pick a role, and a second dongle is
detected and used to run the other source CONCURRENTLY instead of sitting idle.

Pure decision engine — no OS calls — so it unit-tests with no hardware. The live
layer enumerates dongles (`rtl_test`) and spawns rtl_433 / dump1090 on each
assigned device index; it wires onto `plan_sdr_roles()`. Glass-box reasons.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, List, Sequence

_RTL_LINE = re.compile(r"^\s*(\d+):\s*([^,]+),\s*([^,]+),\s*SN:\s*(\S+)\s*$")


class SdrRole(str, Enum):
    ISM = "ism"        # rtl_433 — ISM-band devices (weather, TPMS, meters)
    ADSB = "adsb"      # dump1090 — 1090 MHz ADS-B aircraft
    IDLE = "idle"


_TOOL = {SdrRole.ISM: "rtl_433", SdrRole.ADSB: "dump1090"}


@dataclass(frozen=True)
class SdrDevice:
    index: int
    serial: str = ""
    name: str = ""


@dataclass
class SdrAssignment:
    roles: dict                                     # index -> SdrRole
    reasons: dict = field(default_factory=dict)     # index -> why (glass-box)
    warnings: list = field(default_factory=list)

    def device_for(self, role: SdrRole):
        for index, assigned in self.roles.items():
            if assigned is role:
                return index
        return None


def parse_rtl_devices(text: str) -> List[SdrDevice]:
    """Pure parser of `rtl_test`/`rtl_eeprom` device listing lines like
    '  0:  Realtek, RTL2838UHIDIR, SN: 00000001'."""
    devices: List[SdrDevice] = []
    if not isinstance(text, str):
        return devices
    for line in text.splitlines():
        m = _RTL_LINE.match(line)
        if m:
            devices.append(SdrDevice(index=int(m.group(1)),
                                     name=m.group(3).strip(),
                                     serial=m.group(4).strip()))
    devices.sort(key=lambda d: d.index)
    return devices


def plan_sdr_roles(devices: Iterable[SdrDevice], *,
                   want: Sequence[SdrRole] = (SdrRole.ISM, SdrRole.ADSB)) -> SdrAssignment:
    """Fill the wanted source roles in order, one dongle each; extras idle. A
    lone dongle takes the first wanted role and we note a second is needed to run
    the other source at the same time (one RTL-SDR can't do both)."""
    devices = sorted(devices, key=lambda d: d.index)
    roles = {d.index: SdrRole.IDLE for d in devices}
    reasons: dict = {}
    warnings: list = []

    want = [r for r in want if r in (SdrRole.ISM, SdrRole.ADSB)]
    if not devices:
        warnings.append("no RTL-SDR dongle present")
        return SdrAssignment(roles, reasons, warnings)

    filled = []
    for role, dev in zip(want, devices):
        roles[dev.index] = role
        filled.append(role)
        reasons[dev.index] = (f"{role.value}: {_TOOL[role]} on dongle {dev.index}"
                              + (f" (SN {dev.serial})" if dev.serial else ""))
    for dev in devices[len(want):]:
        reasons[dev.index] = f"idle: spare SDR dongle {dev.index}"

    for role in want:
        if role not in filled:
            warnings.append(
                f"{role.value} ({_TOOL[role]}) needs its own dongle — add another "
                f"RTL-SDR to receive it at the same time as {filled[0].value}")
    return SdrAssignment(roles, reasons, warnings)
