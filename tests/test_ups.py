"""Waveshare UPS (INA219) battery read -> telemetry -> governor shed."""
from redux.core import ups
from redux.core.ups import _swap16
from redux.core.telemetry import ResourceCollector, ResourceMonitor


def _reader(bus_v, current_raw=0):
    """A fake SMBus read_word_data for an INA219 at the given pack voltage/current."""
    raw_v = (round(bus_v / 0.004) << 3) & 0xFFFF
    regs = {0x02: _swap16(raw_v), 0x04: _swap16(current_raw & 0xFFFF)}
    return lambda addr, reg: regs[reg]


def test_read_ina219_recovers_bus_voltage():
    d = ups.read_ina219(_reader(4.0))
    assert abs(d["bus_voltage_v"] - 4.0) < 0.01


def test_battery_percent_single_and_multicell_and_clamp():
    assert ups.battery_percent(4.0) == 80.0               # single cell, 3.2-4.2 range
    assert ups.battery_percent(8.0) == 80.0               # 2-cell pack auto-scales
    assert ups.battery_percent(2.0) == 0.0                # below empty -> clamped
    assert ups.battery_percent(5.0) == 100.0              # above full -> clamped
    assert ups.battery_percent(0) == 0.0


def test_ups_reading_charging_vs_on_battery():
    charging = ups.ups_reading(_reader(4.0, current_raw=500))
    assert charging["charging"] is True and charging["on_battery"] is False
    draining = ups.ups_reading(_reader(3.6, current_raw=0x10000 - 500))   # negative current
    assert draining["charging"] is False and draining["on_battery"] is True


def test_battery_probe_fails_safe_to_none():
    def boom(addr, reg):
        raise OSError("no ack")
    assert ups.battery_probe_from_bus(boom) is None       # never a false reading


def test_collector_includes_battery_when_probed(tmp_path):
    c = ResourceCollector(root=tmp_path, battery_probe=lambda: (42.0, True))
    snap = c.sample()
    assert snap.reading.battery_pct == 42.0 and snap.reading.on_battery is True
    assert snap.provenance.get("battery_pct") == "i2c:ina219 UPS HAT"


def test_collector_without_probe_has_no_battery(tmp_path):
    snap = ResourceCollector(root=tmp_path).sample()
    assert snap.reading.battery_pct is None and snap.reading.on_battery is False


def test_low_battery_on_battery_makes_the_governor_shed(tmp_path):
    rm = ResourceMonitor(collector=ResourceCollector(
        root=tmp_path, battery_probe=lambda: (5.0, True)))   # 5% and discharging
    rm.sample()
    assert rm.decision.interval_scale > 1.0   # cadence stretched -> load shed on low battery
    assert any("battery" in d for d in rm.decision.drivers)   # and battery is the reason
