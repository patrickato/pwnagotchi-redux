"""Resource Governor — heat/power/load shedding with immediate-escalate,
held-recovery, and honest handling of unavailable readings."""
from redux.core import Governor, Mode, Reading


def test_full_when_cool_and_on_mains():
    g = Governor()
    d = g.evaluate(Reading(cpu_temp_c=45.0, cpu_load_pct=20.0, ram_pct=30.0), now=0.0)
    assert d.mode is Mode.FULL and d.interval_scale == 1.0


def test_temperature_thresholds():
    assert Governor().evaluate(Reading(cpu_temp_c=72.0), now=0).mode is Mode.GUARDED
    assert Governor().evaluate(Reading(cpu_temp_c=77.0), now=0).mode is Mode.REDUCED
    d = Governor().evaluate(Reading(cpu_temp_c=81.0), now=0)
    assert d.mode is Mode.SURVIVAL and d.interval_scale == 2.5
    assert "cpu_temp 81" in d.reason


def test_worst_axis_wins():
    # cool temp but RAM critical -> SURVIVAL
    d = Governor().evaluate(Reading(cpu_temp_c=40.0, ram_pct=95.0), now=0)
    assert d.mode is Mode.SURVIVAL and any("ram" in x for x in d.drivers)


def test_battery_only_counts_on_battery():
    # 10% battery but on mains -> not shed
    on_mains = Governor().evaluate(Reading(battery_pct=10.0, on_battery=False), now=0)
    assert on_mains.mode is Mode.FULL
    # same 10% on battery -> REDUCED (<=15)
    on_batt = Governor().evaluate(Reading(battery_pct=10.0, on_battery=True), now=0)
    assert on_batt.mode is Mode.REDUCED


def test_unavailable_readings_do_not_escalate():
    d = Governor().evaluate(Reading(), now=0)   # everything None, on mains
    assert d.mode is Mode.FULL
    assert "no readings available" in d.reason   # honest: not a fabricated 0


def test_immediate_escalation_then_held_recovery():
    g = Governor()
    assert g.evaluate(Reading(cpu_temp_c=82.0), now=0).mode is Mode.SURVIVAL   # escalate now
    # cooled immediately, but recovery is held
    d1 = g.evaluate(Reading(cpu_temp_c=40.0), now=5.0)
    assert d1.mode is Mode.SURVIVAL and d1.holding is True
    assert "holding" in d1.reason
    # still before the 20s hold elapses
    assert g.evaluate(Reading(cpu_temp_c=40.0), now=20.0).mode is Mode.SURVIVAL
    # past the hold -> eases down
    assert g.evaluate(Reading(cpu_temp_c=40.0), now=26.0).mode is Mode.FULL


def test_reescalation_resets_cooldown():
    g = Governor()
    g.evaluate(Reading(cpu_temp_c=82.0), now=0)          # SURVIVAL
    g.evaluate(Reading(cpu_temp_c=40.0), now=5.0)        # cooling, holding
    # heat spikes again before recovery -> stays/returns to SURVIVAL, cooldown cleared
    d = g.evaluate(Reading(cpu_temp_c=85.0), now=10.0)
    assert d.mode is Mode.SURVIVAL and d.holding is False
    assert g._cooldown_since is None
