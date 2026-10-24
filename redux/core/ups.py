"""Read a Waveshare-style UPS HAT (INA219 over I2C): pack voltage, current, and a
derived charge %, so the box can SHOW battery and the Governor can shed load / the
box can shut down safely on low power.

Pure math over an injected 16-bit register reader — unit-testable, no hardware.
The live runtime supplies a real SMBus reader; everything here is calibratable on
the bench (cell chemistry, current sign) without touching the wiring.
"""
from __future__ import annotations

from typing import Callable, Optional

_REG_BUS_VOLTAGE = 0x02
_REG_CURRENT = 0x04
DEFAULT_ADDR = 0x42


def _swap16(word: int) -> int:
    return ((word & 0xFF) << 8) | ((word >> 8) & 0xFF)


def _signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def read_ina219(read_word: Callable[[int, int], int], *, addr: int = DEFAULT_ADDR,
                current_lsb_ma: float = 0.1) -> dict:
    """read_word(addr, reg) behaves like SMBus.read_word_data (little-endian), so we
    byte-swap to the INA219's big-endian registers. Returns pack bus voltage (V) and
    current (mA; sign set by wiring/calibration, + = into the pack = charging)."""
    raw_v = _swap16(read_word(addr, _REG_BUS_VOLTAGE))
    bus_voltage_v = (raw_v >> 3) * 0.004            # INA219: bits [15:3], 4 mV/LSB
    raw_c = _signed16(_swap16(read_word(addr, _REG_CURRENT)))
    return {"bus_voltage_v": round(bus_voltage_v, 3),
            "current_ma": round(raw_c * current_lsb_ma, 1)}


def battery_percent(voltage: float, *, empty_v: float = 3.2, full_v: float = 4.2) -> float:
    """Charge % from pack voltage. Auto-scales for multi-cell packs (voltage / full_v
    rounded to a cell count). Calibrate empty_v/full_v for your chemistry on the bench."""
    if voltage <= 0:
        return 0.0
    cells = max(1, round(voltage / full_v))
    per_cell = voltage / cells
    pct = (per_cell - empty_v) / (full_v - empty_v) * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


def ups_reading(read_word, *, addr: int = DEFAULT_ADDR) -> dict:
    d = read_ina219(read_word, addr=addr)
    charging = d["current_ma"] > 0
    return {"percent": battery_percent(d["bus_voltage_v"]),
            "charging": charging, "on_battery": not charging, **d}


def battery_probe_from_bus(read_word, *, addr: int = DEFAULT_ADDR) -> Optional[tuple]:
    """(percent, on_battery) for the telemetry collector, or None on any read error
    — so a flaky/absent UPS never fabricates a low-battery shed."""
    try:
        r = ups_reading(read_word, addr=addr)
        return r["percent"], r["on_battery"]
    except Exception:
        return None


def smbus_reader(bus_num: int = 1):
    """A real SMBus-backed read_word, or None if smbus2 isn't available (degrade
    honestly rather than import a missing dependency)."""
    try:
        from smbus2 import SMBus  # type: ignore
    except Exception:
        return None

    def read_word(addr, reg):
        with SMBus(bus_num) as bus:
            return bus.read_word_data(addr, reg)
    return read_word
