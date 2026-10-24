"""Glass-box detection of plugged peripherals: is the SDR / Bluetooth / UPS / GPS
actually there *right now*?

Every accessor is injected, so this is fully unit-testable with no hardware, and
each result is `(present, reason)` — never a bare boolean. The live runtime wires
the real sysfs/dev/I2C probes; `Augur.capability_graph()` turns these into graph
providers so the Doctor and the dashboard can honestly say what the box has (and,
by absence, what it is blind to). Nothing is "present" because a module imported —
only because the hardware answered.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

Result = Tuple[bool, str]

# RTL2832U-based RTL-SDR dongles (idVendor:idProduct).
_RTLSDR_IDS = {("0bda", "2838"), ("0bda", "2832"), ("0bda", "2831"),
               ("1d19", "1101"), ("0bda", "2837")}

# INA219 I2C addresses the common Waveshare UPS HATs answer on.
_UPS_I2C_ADDRS = (0x42, 0x43, 0x40, 0x41)


def sdr_present(*, usb_root=Path("/sys/bus/usb/devices")) -> Result:
    try:
        for dev in sorted(usb_root.iterdir()):
            try:
                vid = (dev / "idVendor").read_text().strip().lower()
                pid = (dev / "idProduct").read_text().strip().lower()
            except (OSError, ValueError):
                continue
            if (vid, pid) in _RTLSDR_IDS:
                return True, f"RTL-SDR {vid}:{pid} ({dev.name})"
    except OSError:
        pass
    return False, "no RTL-SDR USB device present"


def bluetooth_present(*, bt_class=Path("/sys/class/bluetooth")) -> Result:
    try:
        hcis = sorted(d.name for d in bt_class.iterdir() if d.name.startswith("hci"))
    except OSError:
        hcis = []
    if hcis:
        return True, f"bluetooth adapter: {', '.join(hcis)}"
    return False, "no bluetooth adapter (no hciN node)"


def gps_present(*, env: Optional[dict] = None, exists: Callable[[str], bool] = os.path.exists) -> Result:
    env = os.environ if env is None else env
    path = env.get("REDUX_GPS")
    if not path:
        return False, "REDUX_GPS not set — attach a USB GPS and point REDUX_GPS at it"
    if exists(path):
        return True, f"GPS device at {path}"
    return False, f"REDUX_GPS={path} but that device is missing"


def ups_present(*, i2c_bus=Path("/dev/i2c-1"),
                probe: Optional[Callable[[int], object]] = None,
                addresses=_UPS_I2C_ADDRS) -> Result:
    """Waveshare UPS HAT = an INA219 on I2C. Presence requires the I2C bus to exist
    AND a probe read to succeed at a known INA219 address. `probe(addr)` is injected
    (the live wiring supplies an smbus reader); without one we can only confirm the
    bus, not the chip — reported honestly, never guessed present."""
    try:
        has_bus = i2c_bus.exists()
    except OSError:
        has_bus = False
    if not has_bus:
        return False, f"no I2C bus ({i2c_bus}) — enable I2C for the UPS HAT"
    if probe is None:
        return False, "I2C bus present; UPS probe unavailable (no SMBus reader)"
    for addr in addresses:
        try:
            probe(addr)
            return True, f"UPS (INA219) at I2C 0x{addr:02x}"
        except Exception:
            continue
    return False, "I2C bus present but no INA219/UPS responded"


def storage_present(*, mounted: Optional[bool] = None, mount="/captures") -> Result:
    """The persistent REDUXCAP capture partition. `mounted` is injected by the live
    runtime (which already tracks the flock/partition identity)."""
    if mounted is None:
        return False, f"{mount} mount state not probed"
    if mounted:
        return True, f"{mount} mounted read-write"
    return False, f"{mount} unavailable — captures/wordlists/scope will not persist"


def _smbus_probe(bus_num: int = 1) -> Optional[Callable[[int], object]]:
    """Build a real INA219 presence probe over smbus2 if available, else None so
    ups_present degrades honestly rather than importing a missing dependency."""
    try:
        from smbus2 import SMBus  # type: ignore
    except Exception:
        return None

    def probe(addr: int):
        with SMBus(bus_num) as bus:
            return bus.read_word_data(addr, 0x00)  # INA219 config register
    return probe


def detect_peripherals(*, mounted: Optional[bool] = None,
                       env: Optional[dict] = None) -> Dict[str, Result]:
    """One live snapshot of (present, reason) per peripheral, using the real system
    accessors. `mounted` lets the live runtime pass through the capture-mount state
    it already knows. Fails safe: any accessor error reads as absent-with-reason."""
    return {
        "sdr": sdr_present(),
        "bluetooth": bluetooth_present(),
        "gps": gps_present(env=env),
        "ups": ups_present(probe=_smbus_probe()),
        "storage": storage_present(mounted=mounted),
    }
