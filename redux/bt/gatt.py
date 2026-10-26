"""BLE GATT looting — connect to a BLE device and read what it gives up.

Once a BLE device is in scope, its GATT server usually hands over a surprising
amount for free: the Device Information service (manufacturer, model, serial,
firmware/hardware/software revisions, system id), the device name, appearance,
battery level, and whatever custom/vendor services the product exposes. This
enumerates the GATT tree, decodes the standard assigned-number UUIDs to names,
reads the readable device-info characteristics, and rolls it into loot — which
also feeds device identification ("it's a Fitbit / a Govee sensor / an Echo").

The UUID knowledge base, the decoding and the loot extraction are pure and
tested with no radio; the live connect runs through an injected enumerator/reader
(bluetoothctl/gatttool on-device) and is scope-gated — LAB runs it, FIELD needs
authorization. A BLE adapter is NEEDS_HARDWARE for the live path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

_SIG_BASE = "-0000-1000-8000-00805f9b34fb"      # the 16-bit→128-bit base UUID suffix

# standard GATT services (16-bit, lowercase)
GATT_SERVICES: Dict[str, str] = {
    "1800": "Generic Access", "1801": "Generic Attribute", "180a": "Device Information",
    "180f": "Battery", "180d": "Heart Rate", "1809": "Health Thermometer",
    "1810": "Blood Pressure", "181c": "User Data", "181a": "Environmental Sensing",
    "1819": "Location and Navigation", "1816": "Cycling Speed and Cadence",
    "1818": "Cycling Power", "180e": "Phone Alert Status", "1811": "Alert Notification",
    "1812": "Human Interface Device", "fee7": "Tencent", "fee0": "Anhui Huami",
    "fe9f": "Google", "feaa": "Eddystone", "fd6f": "Exposure Notification",
}
# standard GATT characteristics (16-bit, lowercase)
GATT_CHARS: Dict[str, str] = {
    "2a00": "Device Name", "2a01": "Appearance", "2a04": "Preferred Conn Params",
    "2a19": "Battery Level", "2a23": "System ID", "2a24": "Model Number",
    "2a25": "Serial Number", "2a26": "Firmware Revision", "2a27": "Hardware Revision",
    "2a28": "Software Revision", "2a29": "Manufacturer Name", "2a2a": "IEEE Reg Cert",
    "2a50": "PnP ID", "2a37": "Heart Rate Measurement", "2a6e": "Temperature",
    "2a6f": "Humidity", "2a19_": "Battery",
}
# which characteristics are loot worth reading, and the GattLoot field they fill
_LOOT_CHARS: Dict[str, str] = {
    "2a00": "device_name", "2a29": "manufacturer", "2a24": "model",
    "2a25": "serial", "2a26": "firmware", "2a27": "hardware",
    "2a28": "software", "2a23": "system_id", "2a19": "battery", "2a01": "appearance",
}


def short_uuid(uuid: str) -> str:
    """Normalize a UUID to its 16-bit short form when it's a SIG base UUID, else
    return the full lowercased UUID."""
    u = (uuid or "").strip().lower()
    if len(u) == 36 and u.endswith(_SIG_BASE) and u[4:8] == "0000":
        return u[:8].lstrip("0") or "0"
    if len(u) == 36 and u.endswith(_SIG_BASE):
        return u[4:8]
    return u


def describe_uuid(uuid: str, *, kind: str = "char") -> str:
    """Human name for a UUID (service or characteristic), or 'vendor-specific'."""
    s = short_uuid(uuid)
    table = GATT_SERVICES if kind == "service" else GATT_CHARS
    if s in table:
        return table[s]
    # also try the other table (callers sometimes don't know which)
    other = GATT_CHARS if kind == "service" else GATT_SERVICES
    if s in other:
        return other[s]
    return f"vendor-specific ({s})"


@dataclass(frozen=True)
class Characteristic:
    uuid: str
    properties: tuple = ()          # ("read","write","notify","indicate")
    value: Optional[str] = None     # decoded string value, if read

    @property
    def name(self) -> str:
        return describe_uuid(self.uuid, kind="char")

    @property
    def readable(self) -> bool:
        return "read" in self.properties


@dataclass(frozen=True)
class Service:
    uuid: str
    characteristics: tuple = ()

    @property
    def name(self) -> str:
        return describe_uuid(self.uuid, kind="service")

    @property
    def vendor_specific(self) -> bool:
        return self.name.startswith("vendor-specific")


@dataclass(frozen=True)
class GattLoot:
    mac: str
    device_name: str = ""
    manufacturer: str = ""
    model: str = ""
    serial: str = ""
    firmware: str = ""
    hardware: str = ""
    software: str = ""
    system_id: str = ""
    battery: str = ""
    appearance: str = ""
    services: tuple = ()            # list of {uuid,name,vendor_specific,chars:[...]}
    custom_services: tuple = ()     # vendor-specific service UUIDs
    note: str = ""

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        return d


def extract_loot(mac: str, services: Sequence[Service]) -> GattLoot:
    """Roll enumerated services + any read characteristic values into GattLoot."""
    fields: Dict[str, str] = {}
    svc_rows = []
    custom = []
    for svc in services or ():
        chars = []
        for ch in svc.characteristics or ():
            s = short_uuid(ch.uuid)
            if ch.value is not None and s in _LOOT_CHARS:
                fields[_LOOT_CHARS[s]] = str(ch.value)
            chars.append({"uuid": ch.uuid, "name": ch.name,
                          "properties": list(ch.properties), "value": ch.value})
        svc_rows.append({"uuid": svc.uuid, "name": svc.name,
                         "vendor_specific": svc.vendor_specific, "chars": chars})
        if svc.vendor_specific:
            custom.append(svc.uuid)
    looted = [k for k in ("device_name", "manufacturer", "model", "serial", "firmware")
              if fields.get(k)]
    note = (f"looted {len(looted)} device-info field(s) across {len(svc_rows)} service(s)"
            if svc_rows else "no GATT services enumerated")
    return GattLoot(mac=(mac or "").lower(), services=tuple(svc_rows),
                    custom_services=tuple(custom), note=note, **fields)


@dataclass
class GattLooter:
    """Enumerate + loot a BLE device's GATT. enumerator/reader are injected for
    tests; on-device they wrap bluetoothctl/gatttool through `runner`."""
    runner: Optional[Callable] = None

    def plan(self, mac: str) -> List[str]:
        return [f"bluetoothctl --timeout 5 connect {mac}",
                f"gatttool -b {mac} --primary",
                f"gatttool -b {mac} --characteristics",
                f"# read each readable device-info characteristic"]

    def loot(self, mac: str, *, scope=None, authorized: bool = False, lab: bool = False,
             enumerator: Optional[Callable[[str], Sequence[Service]]] = None,
             now: Optional[float] = None):
        """Gate → enumerate → read → roll up. Returns (GattLoot|None, reason)."""
        if not lab:
            if scope is not None:
                try:
                    ok, why = scope.authorize(bssid=mac, now=now)
                except Exception:
                    ok, why = False, "scope check failed"
                if not ok and not authorized:
                    return None, f"refused: {why}"
            elif not authorized:
                return None, "refused: GATT loot needs --authorized in FIELD (or LAB mode)"
        if enumerator is None:
            return None, "no BLE enumerator available (needs a Bluetooth adapter on-device)"
        try:
            services = list(enumerator(mac))
        except Exception as e:
            return None, f"GATT enumerate failed: {type(e).__name__}"
        loot = extract_loot(mac, services)
        return loot, loot.note


def to_identify_signals(loot: GattLoot):
    """Feed GATT loot into device identification — name + manufacturer + model."""
    from ..intel.device_id import DeviceSignals
    return DeviceSignals(mac=loot.mac, vendor=loot.manufacturer,
                         ble_name=loot.device_name or loot.model)
