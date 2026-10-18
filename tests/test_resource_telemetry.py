"""Synthetic file fixtures only; these tests make no Pi hardware claims."""
import json
import subprocess

import pytest

from redux.core.telemetry import ResourceCollector, Snapshot, _throttled
from redux.core.governor import Governor, Reading, Mode
from redux.core.doctor import DoctorInputs, probe_thermal_power, Status
from redux.core.augur import Augur


class Clock:
    now = 100.
    def __call__(self):
        return self.now


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


@pytest.fixture
def rig(tmp_path):
    clock = Clock()
    put(tmp_path, 'proc/device-tree/model', 'Raspberry Pi 4 Model B\0')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/type', 'cpu-thermal')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/temp', '45000')
    put(tmp_path, 'sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq', '1500000')
    put(tmp_path, 'proc/stat', 'cpu 10 0 10 80 0 0 0 0 5 0\n')
    put(tmp_path, 'proc/meminfo', 'MemTotal: 1000 kB\nMemAvailable: 700 kB\n')
    return tmp_path, clock, ResourceCollector(tmp_path, clock=clock, monotonic=clock,
                                            throttle_reader=lambda: 0)


def finding(snapshot, now=100):
    g = Governor().evaluate(snapshot.reading, now)
    return probe_thermal_power(DoctorInputs(governor=g, telemetry=snapshot.status(now, now)))


def test_valid_readings_cache_and_cpu_delta(rig):
    root, clock, collector = rig
    first = collector.sample()
    assert first.reading == Reading(cpu_temp_c=45, ram_pct=30)
    assert first.cpu_frequency_mhz == 1500
    assert first.throttled_mask == 0
    assert finding(first).status is Status.OK
    assert collector.sample() is None
    clock.now += 2
    put(root, 'proc/stat', 'cpu 20 0 20 100 0 0 0 0 10 0\n')
    sample = collector.sample()
    assert sample.reading.cpu_load_pct == 50
    status = collector.status()
    assert status['fresh'] and status['values']['on_battery'] is None
    assert status['values']['battery_pct'] is None
    assert status['flags']['undervoltage'] is False
    assert 'cpu_load_pct' in status['provenance']
    json.dumps(status, allow_nan=False)


@pytest.mark.parametrize('value', ['nan', 'inf', '-100000', '999999', 'oops', ''])
def test_bad_temperature(rig, value):
    root, _, collector = rig
    put(root, 'sys/class/thermal/thermal_zone0/temp', value)
    snapshot = collector.sample()
    assert snapshot.reading.cpu_temp_c is None
    assert finding(snapshot).status is Status.UNKNOWN


def test_absent_non_pi_and_no_subprocess(tmp_path):
    def forbidden():
        raise AssertionError('firmware command on non-Pi')
    collector = ResourceCollector(tmp_path, throttle_reader=forbidden)
    snapshot = collector.sample()
    assert snapshot.reading == Reading()
    assert snapshot.throttled_mask is None
    assert probe_thermal_power(DoctorInputs(governor=Governor().evaluate(Reading(), 0))).status is Status.UNKNOWN


@pytest.mark.parametrize('age', [-1, 10.01, 1000, float('nan')])
def test_stale_or_future_snapshot_is_unknown(rig, age):
    _, _, collector = rig
    snapshot = collector.sample()
    assert finding(snapshot, 100 + age).status is Status.UNKNOWN


def test_monotonic_staleness_and_clock_rollback(rig):
    _, _, c = rig
    snapshot = c.sample()
    assert not snapshot.status(100, 111)['fresh']
    assert not snapshot.status(99, 101)['fresh']


def test_firmware_active_history_and_unavailable(rig):
    _, _, c = rig
    c.throttle_reader = lambda: 1
    assert finding(c.sample()).status is Status.DEGRADED
    c.snapshot = None
    c.throttle_reader = lambda: 1 << 16
    s = c.sample()
    assert s.status(100, 100)['flags']['undervoltage_occurred'] is True
    assert finding(s).status is Status.OK  # history is not an active fault
    c.snapshot = None
    c.throttle_reader = lambda: None
    assert finding(c.sample()).status is Status.UNKNOWN


@pytest.mark.parametrize('contents', ['MemTotal: 0 kB\nMemAvailable: 0 kB',
    'MemTotal: 100 kB\nMemAvailable: 101 kB', 'MemTotal: x kB',
    'MemTotal: 100 kB\nMemAvailable: -1 kB', 'x' * 17000])
def test_bad_memory(rig, contents):
    root, _, c = rig
    put(root, 'proc/meminfo', contents)
    assert c.sample().reading.ram_pct is None


def test_cpu_reset_missing_and_first_sample(rig):
    root, clock, c = rig
    c.sample()
    for stat in ['cpu 1 0 1 2 0 0 0 0', 'garbage', 'cpu 10 0 10 80 0 0 0 0']:
        clock.now += 2
        put(root, 'proc/stat', stat)
        assert c.sample().reading.cpu_load_pct is None


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1, 101, 'hot', True])
def test_governor_rejects_invalid_percentages(bad):
    d = Governor().evaluate(Reading(cpu_load_pct=bad, ram_pct=bad, battery_pct=bad, on_battery=True), 0)
    assert d.mode is Mode.FULL and not d.observed_axes


def test_augur_lifecycle_status_is_read_only_and_stale(rig):
    _, clock, c = rig
    augur = Augur(radios=[], resource_collector=c)
    assert augur.status(processing_report={})['telemetry'] is None
    augur.pump()
    s = augur.status(processing_report={})
    assert s['telemetry']['values']['cpu_temp_c'] == 45
    assert augur._doctor_inputs().telemetry['fresh']
    old = c.snapshot
    for _ in range(3):
        augur.status(processing_report={})
    assert c.snapshot is old
    clock.now += 11
    assert probe_thermal_power(augur._doctor_inputs()).status is Status.UNKNOWN
    augur.pump()
    assert augur.telemetry_status()['fresh']
    augur.observe_resources(Reading(cpu_temp_c=82), now=clock.now)
    assert augur._gov.mode is Mode.SURVIVAL
    assert probe_thermal_power(augur._doctor_inputs()).status is Status.ACTION_REQUIRED
    clock.now += 11
    assert probe_thermal_power(augur._doctor_inputs()).status is Status.UNKNOWN


@pytest.mark.parametrize('out,code,expected', [('throttled=0x50005\n',0,0x50005),
    ('throttled=0x0',0,0), ('throttled=0x0',1,None), ('junk',0,None)])
def test_firmware_command(monkeypatch, out, code, expected):
    def run(args, **kwargs):
        assert args == ['/usr/bin/vcgencmd', 'get_throttled']
        assert kwargs['timeout'] == .5
        return subprocess.CompletedProcess(args, code, out, '')
    monkeypatch.setattr(subprocess, 'run', run)
    assert _throttled() == expected


def test_firmware_command_timeout(monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired('vcgencmd', .5)
    monkeypatch.setattr(subprocess, 'run', run)
    assert _throttled() is None


def test_disappearing_sensor_replaces_previous_value(rig):
    root, clock, c = rig
    c.sample()
    clock.now += 2
    (root / 'sys/class/thermal/thermal_zone0/temp').unlink()
    s = c.sample()
    assert s.reading.cpu_temp_c is None
    assert 'cpu_temp_c' not in s.provenance
    assert finding(s, clock.now).status is Status.UNKNOWN


def test_invalid_timestamp_serializes_as_unavailable(rig):
    _, _, c = rig
    s = c.sample()
    invalid = Snapshot(float('nan'), 100, s.reading, None, None, {})
    result = invalid.status(100, 100)
    assert not result['fresh'] and result['measured_at'] is None
    json.dumps(result, allow_nan=False)


def test_governor_temperature_and_recovery_from_collector(rig):
    root, clock, c = rig
    g = Governor()
    for temp, expected in [(70000, Mode.GUARDED), (76000, Mode.REDUCED), (80000, Mode.SURVIVAL)]:
        put(root, 'sys/class/thermal/thermal_zone0/temp', str(temp))
        s = c.sample()
        assert g.evaluate(s.reading, clock.now).mode is expected
        clock.now += 2
    put(root, 'sys/class/thermal/thermal_zone0/temp', '45000')
    assert g.evaluate(c.sample().reading, clock.now).holding
    clock.now += 21
    assert g.evaluate(c.sample().reading, clock.now).mode is Mode.FULL


def test_active_firmware_does_not_downgrade_survival(rig):
    root, _, c = rig
    put(root, 'sys/class/thermal/thermal_zone0/temp', '82000')
    c.throttle_reader = lambda: 1
    assert finding(c.sample()).status is Status.ACTION_REQUIRED


def test_active_firmware_with_no_other_axes(rig):
    root, _, c = rig
    for path in ['sys/class/thermal/thermal_zone0/temp', 'proc/stat', 'proc/meminfo']:
        (root / path).unlink()
    c.throttle_reader = lambda: 1
    assert finding(c.sample()).status is Status.DEGRADED


def test_web_payload_preserves_telemetry_and_no_resampling(rig):
    from redux.web import status_payload, render_page
    _, _, c = rig
    augur = Augur(radios=[], resource_collector=c)
    augur.pump()
    snapshot = c.snapshot
    data = status_payload(augur, processing_report={})
    assert data['telemetry'] == c.status()
    assert c.snapshot is snapshot
    page = render_page()
    assert 'Hardware telemetry' in page
    assert page.index('Live Augur persona') < page.index('Hardware telemetry')
    assert "document.getElementById('resourcevalues').textContent" in page
    assert 'STALE / invalid' in page


def test_live_checkpoint_refreshes_freshness_without_visual_resample(rig, tmp_path, monkeypatch):
    from redux.core import live_runtime as live
    root, clock, c = rig
    augur = Augur(radios=[], resource_collector=c)
    augur.pump()
    runtime = live.LiveRuntime(live.LiveConfig(
        state_dir=tmp_path / 'state', capture_dir=tmp_path / 'captures',
        active_dir=tmp_path / 'active', enable_web=False, status_sample_seconds=30),
        radio_probe=lambda: [], clock=clock)
    runtime.augur = augur
    calls = []
    def visual(agent, **kwargs):
        calls.append(1)
        return {'telemetry': agent.telemetry_status()}
    monkeypatch.setattr(live, 'status_payload', visual)
    try:
        runtime._checkpoint()
        first = runtime._snapshot
        assert first['telemetry']['fresh']
        clock.now += 11
        runtime._checkpoint()
        assert len(calls) == 1
        assert not runtime._snapshot['telemetry']['fresh']
        assert first['telemetry']['fresh']  # snapshots stay immutable to readers
    finally:
        runtime.close()


@pytest.mark.parametrize('timestamp', ['bad', None, True, 10**1000, float('inf')])
def test_malformed_timestamp_remains_unknown(rig, timestamp):
    _, _, c = rig
    s = c.sample()
    result = Snapshot(timestamp, 100, s.reading, None, None, {}).status(100, 100)
    assert result['measured_at'] is None and not result['fresh']
    json.dumps(result, allow_nan=False)


def test_huge_external_value_is_unavailable():
    d = Governor().evaluate(Reading(cpu_temp_c=10**1000), 0)
    assert d.mode is Mode.FULL and not d.observed_axes


def test_manual_battery_observation_is_preserved(rig):
    _, clock, c = rig
    augur = Augur(radios=[], resource_collector=c)
    augur.observe_resources(Reading(battery_pct=10, on_battery=True), now=clock.now)
    data = augur.telemetry_status()
    assert data['values']['battery_pct'] == 10
    assert data['values']['on_battery'] is True
    assert augur._gov.mode is Mode.REDUCED
    f = probe_thermal_power(augur._doctor_inputs())
    assert f.status is Status.DEGRADED
    assert 'unmeasured' not in f.reason


@pytest.mark.parametrize('bad', [[], 'bad', {'fresh':True,'values':[]},
    {'fresh':True,'values':{},'flags':'bad'}, {'fresh':True,'values':{},'age_seconds':'bad'}])
def test_doctor_malformed_telemetry_is_unknown(bad):
    g = Governor().evaluate(Reading(cpu_temp_c=40),100)
    assert probe_thermal_power(DoctorInputs(governor=g,telemetry=bad)).status is Status.UNKNOWN


def test_optional_snapshot_fields_and_overflow_cannot_break_json():
    for frequency, mask in [(float('nan'),'bad'), (True, -1), ('bad',float('inf'))]:
        s = Snapshot(-1e308, 1e308, Reading(), frequency, mask, None)
        result = s.status(1e308, -1e308)
        assert result['fresh'] is False
        assert result['age_seconds'] is None
        assert result['values']['cpu_frequency_mhz'] is None
        assert result['values']['throttled_mask'] is None
        json.dumps(result, allow_nan=False)


def test_resource_state_survives_new_augur_and_uses_monotonic_recovery(rig):
    from redux.core.telemetry import ResourceMonitor
    root, clock, c = rig
    monitor = ResourceMonitor(c)
    put(root, 'sys/class/thermal/thermal_zone0/temp','82000')
    a = Augur(radios=[], resource_monitor=monitor)
    a.pump()
    assert a._gov.mode is Mode.SURVIVAL
    c.clock = lambda: 1000000  # wall-clock step cannot end a recovery hold
    clock.now += 2
    put(root, 'sys/class/thermal/thermal_zone0/temp','45000')
    b = Augur(radios=[], resource_monitor=monitor)
    b.pump()
    assert b._gov.mode is Mode.SURVIVAL and b._gov.holding
    assert a._gov is b._gov
    clock.now += 21
    b.pump()
    assert b._gov.mode is Mode.FULL
    assert b.telemetry_status()['values']['cpu_load_pct'] is None  # stalled ticks


def test_no_radio_runtime_still_samples_without_engine_or_radio_actions(rig, tmp_path, monkeypatch):
    from redux.core import live_runtime as live
    from redux.core.telemetry import ResourceMonitor
    _, clock, c = rig
    runtime = live.LiveRuntime(live.LiveConfig(state_dir=tmp_path/'state',
        capture_dir=tmp_path/'captures',active_dir=tmp_path/'active',enable_web=False),
        clock=clock, resource_monitor=ResourceMonitor(c), radio_probe=lambda: [])
    runtime.next_try = 1e10  # retry gate closed: no probe/launch action
    def forbidden(*args,**kwargs):
        raise AssertionError('hardware action attempted')
    runtime.executor = forbidden
    runtime.spawn = forbidden
    try:
        runtime.tick()
        snapshot = runtime._snapshot
        assert snapshot['telemetry']['values']['cpu_temp_c'] == 45
        thermal = next(f for f in snapshot['doctor']['findings'] if f['area'] == 'thermal/power')
        assert thermal['status'] == 'ok'
        assert runtime.augur is None and runtime.process is None
        json.dumps(snapshot, allow_nan=False)
    finally:
        runtime.close()


def test_governor_does_not_treat_truthy_power_source_as_battery():
    d = Governor().evaluate(Reading(on_battery='unknown',battery_pct=1),100)
    assert d.mode is Mode.FULL and 'battery' not in d.observed_axes


def test_complete_status_to_field_health_flow(rig):
    import importlib.util
    from pathlib import Path
    from redux.web import status_payload
    spec = importlib.util.spec_from_file_location('flow_field_status',
        Path(__file__).resolve().parents[1] / 'scripts/field-status.py')
    field = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(field)
    root, clock, c = rig
    augur = Augur(radios=[], resource_collector=c)
    def response():
        result = status_payload(augur, processing_report={})
        result['doctor'] = augur.doctor_report()
        result['runtime'] = {'updated_utc':clock.now,'state':'running'}
        return json.loads(json.dumps(result,allow_nan=False))
    assert field.sanitize(response(),clock.now)['telemetry'] is None
    augur.pump()
    clean = field.sanitize(response(),clock.now)
    assert clean['telemetry']['fresh']
    assert clean['telemetry']['values']['cpu_temp_c'] == 45
    put(root,'sys/class/thermal/thermal_zone0/temp','82000')
    clock.now += 2
    augur.pump()
    clean = field.sanitize(response(),clock.now)
    verdicts = {name:state for name,state,_ in field.assess(clean)}
    assert clean['areas']['thermal/power'] == 'action'
    assert verdicts['Doctor thermal assessment'] == 'FAIL'
    clock.now += 11
    clean = field.sanitize(response(),clock.now)
    assert clean['areas']['thermal/power'] == 'unknown'
    assert not clean['telemetry']['fresh']
    put(root,'sys/class/thermal/thermal_zone0/temp','malformed')
    augur.pump()
    clean = field.sanitize(response(),clock.now)
    assert clean['telemetry']['values']['cpu_temp_c'] is None
    assert clean['telemetry']['values']['battery_pct'] is None
    assert clean['telemetry']['values']['on_battery'] is None


def test_governor_checkpoint_does_not_lag_telemetry_in_visual_cache(rig, tmp_path):
    from redux.core import live_runtime as live
    from redux.core.telemetry import ResourceMonitor
    root, clock, c = rig
    monitor = ResourceMonitor(c)
    augur = Augur(radios=[], resource_monitor=monitor)
    runtime = live.LiveRuntime(live.LiveConfig(state_dir=tmp_path/'state',
        capture_dir=tmp_path/'captures',active_dir=tmp_path/'active',
        status_sample_seconds=30,enable_web=False),clock=clock,resource_monitor=monitor,
        radio_probe=lambda: [])
    runtime.augur = augur
    try:
        augur.pump()
        runtime._checkpoint()
        revision = runtime._snapshot['runtime']['visual_revision']
        assert runtime._snapshot['governor']['mode'] == 'full'
        clock.now += 2
        put(root,'sys/class/thermal/thermal_zone0/temp','82000')
        augur.pump()
        runtime._checkpoint()
        result = runtime._snapshot
        assert result['runtime']['visual_revision'] == revision
        assert result['governor']['mode'] == 'survival'
        assert result['governor']['measurement_fresh'] is True
        assert 'cpu_temp' in result['governor']['observed_axes']
        assert result['telemetry']['values']['cpu_temp_c'] == 82
        thermal = next(f for f in result['doctor']['findings'] if f['area']=='thermal/power')
        assert thermal['status'] == 'action'
    finally:
        runtime.close()


def test_malformed_power_source_never_reports_mains():
    result = Snapshot(100,100,Reading(on_battery='unknown',battery_pct=10),None,None,{}).status(100,100)
    assert result['values']['on_battery'] is None
