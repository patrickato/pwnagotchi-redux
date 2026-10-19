"""Multi-stage software acceptance: real supervisor/Augur/Doctor/UI, fake devices.

No Bettercap binary, physical interfaces, packet reception or Pi operations.
"""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from redux.core import live_runtime as live
from redux.core.telemetry import ResourceCollector, ResourceMonitor
from redux.display import fbui
from test_live_runtime import cfg, radio, Child, Transport
from test_resource_telemetry import put


def area(snapshot, name):
    return next(row for row in snapshot['doctor']['findings'] if row['area'] == name)


@pytest.fixture
def platform(tmp_path, monkeypatch):
    clock = [1000.0]
    radios, children, scans = [], [], []
    sensor = tmp_path / 'sensors'
    put(sensor, 'proc/device-tree/model', 'Raspberry Pi 4 Model B\0')
    put(sensor, 'sys/class/thermal/thermal_zone0/type', 'cpu-thermal')
    put(sensor, 'sys/class/thermal/thermal_zone0/temp', '45000')
    put(sensor, 'proc/stat', 'cpu 10 0 10 80 0 0 0 0\n')
    put(sensor, 'proc/meminfo', 'MemTotal: 1000 kB\nMemAvailable: 700 kB\n')
    collector = ResourceCollector(sensor, clock=lambda: clock[0], monotonic=lambda: clock[0], throttle_reader=lambda: 0)
    resources = ResourceMonitor(collector)
    worker = {'available': True, 'by_status': {}, 'artifacts': 0, 'hash_records': 0,
              'last_scan': {'completed_utc': 1000, 'scanned': 0, 'outcomes': {}}}
    failures = {'ledger': False}
    def summary(path):
        scans.append(clock[0])
        if failures['ledger']:
            raise sqlite3.OperationalError('synthetic lock')
        return worker.copy()
    def spawn(*a, **kw):
        child = Child()
        children.append(child)
        return child
    monkeypatch.setattr(live.time, 'time', lambda: clock[0])
    monkeypatch.setattr(live, 'HttpTransport', Transport)
    monkeypatch.setattr(live, 'read_link_type', lambda iface: 803 if iface else None)
    monkeypatch.setattr(live, 'read_summary', summary)
    monkeypatch.setattr(live, 'free_bytes', lambda path: 2**30)
    monitor = lambda *a, **k: SimpleNamespace(returncode=0, stdout='type monitor\n', stderr='')
    runtime = live.LiveRuntime(cfg(tmp_path, status_sample_seconds=5),
        radio_probe=lambda: radios[:], spawn=spawn, executor=monitor,
        clock=lambda: clock[0], resource_monitor=resources)
    result = SimpleNamespace(runtime=runtime, clock=clock, radios=radios, children=children,
        collector=collector, sensor=sensor, scans=scans, failures=failures, worker=worker)
    yield result
    if not runtime._lock.closed:
        runtime.close()


def tick(p, advance=1):
    p.clock[0] += advance
    return p.runtime.tick()


def start(p):
    p.radios.append(radio())
    p.clock[0] = max(p.clock[0] + 1, p.runtime.next_try)
    assert p.runtime.tick() == 'starting_engine'
    assert tick(p) == 'running'
    assert tick(p) == 'running'


def test_cold_start_radio_sensor_worker_recovery_and_shutdown(platform):
    p, runtime = platform, platform.runtime
    assert fbui.status_view(runtime._snapshot, now=p.clock[0])['state'] == 'STARTING'
    assert tick(p) == 'degraded'
    assert runtime.process is None
    assert runtime._snapshot['ui']['heartbeat']['state'] == 'fresh'
    start(p)
    monitor = runtime.resources
    assert runtime.created == 2  # fixture events, not verified packets
    assert runtime.handoffs == 0
    assert area(runtime._snapshot, 'thermal/power')['status'] == 'ok'
    assert runtime._snapshot['ui']['sensors']['state'] == 'fresh'
    assert 'unverified' in next(r['value'] for r in runtime._snapshot['ui']['rows'] if r['label'] == 'Capture path')
    child = runtime.process
    put(p.sensor, 'sys/class/thermal/thermal_zone0/temp', '85000')
    tick(p, 3)
    assert runtime._snapshot['governor']['mode'] == 'survival'
    assert area(runtime._snapshot, 'thermal/power')['status'] == 'action'
    assert runtime.process is child  # no new capture throttling/control
    (p.sensor / 'sys/class/thermal/thermal_zone0/temp').unlink()
    tick(p, 3)
    assert runtime._snapshot['telemetry']['values']['cpu_temp_c'] is None
    assert area(runtime._snapshot, 'thermal/power')['status'] != 'ok'
    put(p.sensor, 'sys/class/thermal/thermal_zone0/temp', '45000')
    tick(p, 30)
    tick(p, 21)
    assert runtime._snapshot['governor']['mode'] == 'full'
    assert runtime.process is child
    # Physical removal, bounded retry, then a new engine generation.
    p.radios.clear()
    tick(p, 31)
    assert runtime.state == 'degraded' and child.dead
    assert runtime._snapshot['runtime']['visual_sampled_utc'] is None
    assert runtime.handoffs == 0  # configured but absent file is never queued
    start(p)
    assert runtime.resources is monitor
    assert runtime.process is not child
    record = json.loads((runtime.config.state_dir / 'live.json').read_text())
    assert record['observability']['schema'] == 1
    assert record['observability']['sensor_freshness'] == 'fresh'
    runtime.close()
    assert runtime._lock.closed
    assert all(child.dead for child in p.children)
    assert runtime._snapshot['runtime']['state'] == 'stopped'
    assert fbui.render(runtime._snapshot, page='system', now=p.clock[0]).size == (480, 320)
    replacement = live.LiveRuntime(runtime.config, radio_probe=lambda: [])
    replacement.close()  # exclusive ownership was actually released


def test_independent_observer_failures_do_not_restart_engine(platform, monkeypatch):
    p, runtime = platform, platform.runtime
    start(p)
    child = runtime.process
    p.failures['ledger'] = True
    tick(p, 6)
    assert area(runtime._snapshot, 'capture processing')['status'] == 'degraded'
    assert runtime._snapshot['runtime']['observer_errors']['processing'] == 'OperationalError'
    calls = len(p.scans)
    for _ in range(4):
        tick(p, .2)
    assert len(p.scans) == calls
    sample = p.collector.sample
    monkeypatch.setattr(p.collector, 'sample', lambda: (_ for _ in ()).throw(OSError('sensor disconnected')))
    tick(p, 3)
    assert runtime._snapshot['telemetry']['fresh'] is False
    assert area(runtime._snapshot, 'thermal/power')['status'] == 'unknown'
    assert runtime.process is child and not child.dead
    p.failures['ledger'] = False
    monkeypatch.setattr(p.collector, 'sample', sample)
    p.worker['last_scan']['completed_utc'] = p.clock[0]
    tick(p, 6)
    assert runtime._snapshot['capture_processing']['available'] is True
    assert runtime._snapshot['telemetry']['fresh'] is True
    assert runtime._snapshot['runtime']['observer_errors'] == {}
    assert runtime.process is child


def test_checkpoint_failure_is_immediate_bounded_and_recovers(platform, monkeypatch):
    p, runtime = platform, platform.runtime
    start(p)
    checkpoint, writes = live.atomic_checkpoint, []
    def broken(*args):
        writes.append(p.clock[0])
        raise OSError('synthetic read-only storage')
    monkeypatch.setattr(live, 'atomic_checkpoint', broken)
    tick(p, 6)
    assert area(runtime._snapshot, 'runtime checkpoint')['status'] == 'degraded'
    assert runtime.last_error == ''
    assert runtime._snapshot['runtime']['persistence_error']
    for _ in range(10):
        tick(p, .1)
    assert len(writes) == 1
    monkeypatch.setattr(live, 'atomic_checkpoint', checkpoint)
    tick(p, 6)
    assert runtime.persistence_error == ''
    assert runtime._snapshot['runtime']['persistence_error'] == ''
    assert not any(f['area'] == 'runtime checkpoint' for f in runtime._snapshot['doctor']['findings'])
    assert json.loads((runtime.config.state_dir / 'live.json').read_text())['persistence_error'] == ''


def test_engine_restart_rotation_and_database_backpressure(platform, monkeypatch):
    p, runtime = platform, platform.runtime
    start(p)
    monitor, first = runtime.resources, runtime.process
    flush = runtime.augur.flush_sightings
    monkeypatch.setattr(runtime.augur, 'flush_sightings', lambda: (_ for _ in ()).throw(sqlite3.OperationalError('locked')))
    assert tick(p) == 'telemetry_degraded'
    assert runtime.process is first
    assert area(runtime._snapshot, 'live engine')['status'] == 'degraded'
    monkeypatch.setattr(runtime.augur, 'flush_sightings', flush)
    assert tick(p, 4) == 'running'
    first.dead = True
    assert tick(p) == 'degraded'
    p.clock[0] = runtime.next_try
    assert runtime.tick() == 'starting_engine'
    assert tick(p) == 'running'
    second = runtime.process
    assert second is not first and runtime.resources is monitor
    runtime.started = p.clock[0] - runtime.config.rotation_seconds
    assert tick(p) == 'rotating'
    assert second.dead and runtime.handoffs == 0
    assert tick(p) == 'starting_engine'
    assert tick(p) == 'running'
    assert runtime.resources is monitor
    runtime.close()
    runtime.close()  # idempotent cleanup after repeated lifecycle requests
    assert all(child.dead for child in p.children)


@pytest.mark.parametrize('bad', [None, [], 'bad', {'completed_utc': 1000, 'scanned': 2, 'outcomes': {'ready': 1}}])
def test_worker_interruption_contradictions_and_recovery(platform, bad):
    p, runtime = platform, platform.runtime
    start(p)
    child = runtime.process
    p.worker['last_scan'] = bad
    tick(p, 6)
    assert area(runtime._snapshot, 'capture processing')['status'] != 'ok'
    p.worker['last_scan'] = {'completed_utc': p.clock[0] - 400, 'scanned': 0, 'outcomes': {}}
    tick(p, 6)
    assert area(runtime._snapshot, 'capture processing')['status'] == 'degraded'
    p.worker['last_scan']['completed_utc'] = p.clock[0] + 6
    tick(p, 6)
    assert area(runtime._snapshot, 'capture processing')['status'] == 'ok'
    assert runtime.process is child
    assert runtime.handoffs == 0


def test_status_producers_fail_independently_retry_and_recover(platform, monkeypatch):
    p, runtime = platform, platform.runtime
    start(p)
    payload, calls = live.status_payload, []
    def fail(*a, **k):
        calls.append(p.clock[0])
        raise RuntimeError('visual DB unavailable')
    monkeypatch.setattr(live, 'status_payload', fail)
    tick(p, 6)
    assert runtime._snapshot['runtime']['visual_sampled_utc'] is None
    assert runtime._snapshot['ui']['visual']['state'] == 'unknown'
    for _ in range(10):
        tick(p, .1)
    assert len(calls) == 1
    doctor = runtime.augur.doctor_report
    monkeypatch.setattr(runtime.augur, 'doctor_report', lambda: {'findings': 'malformed'})
    tick(p)
    assert 'observer doctor' in runtime._snapshot['doctor']['coverage']['not_assessed']
    monkeypatch.setattr(runtime.augur, 'doctor_report', doctor)
    monkeypatch.setattr(live, 'status_payload', payload)
    tick(p, 6)
    assert runtime._snapshot['runtime']['observer_errors'] == {}
    assert runtime._snapshot['ui']['visual']['state'] == 'fresh'


def test_http_startup_recovery_and_provider_failure_are_truthful(platform):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from redux.web.status_page import make_handler
    p, runtime = platform, platform.runtime
    failed = [False]
    def provider():
        if failed[0]:
            raise OSError('private producer details must not leak')
        return runtime._snapshot
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(provider))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def get():
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/status', timeout=3) as response:
            return json.load(response)
    try:
        assert get()['runtime']['state'] == 'starting'
        assert get()['ui']['sensors']['state'] == 'unknown'
        start(p)
        assert get()['runtime']['state'] == 'running'
        assert get()['ui']['sensors']['state'] == 'fresh'
        failed[0] = True
        with pytest.raises(urllib.error.HTTPError) as error:
            get()
        assert error.value.code == 503
        assert error.value.read() == b'{"error":"status unavailable"}'
        failed[0] = False
        assert get()['runtime']['state'] == 'running'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize('state', ['lease_blocked', 'termination_pending'])
def test_safety_gate_labels_agree_across_doctor_native_and_web_contract(platform, state):
    from redux.display.radio_panel import model as radio_model
    p, runtime = platform, platform.runtime
    runtime.state = state
    runtime.iface = 'wlan1mon'
    runtime._checkpoint()
    assert area(runtime._snapshot, 'live engine')['status'] == 'action'
    assert fbui.status_view(runtime._snapshot, now=p.clock[0])['health'] == 'action'
    assert radio_model(runtime._snapshot, now=p.clock[0])['capture']['state'] == state.upper()


def test_inventory_thread_start_failure_is_observable_without_losing_owner(platform, monkeypatch):
    p, runtime = platform, platform.runtime
    start(p)
    child = runtime.process
    runtime.radio_inventory_provider = lambda: {}
    real_thread = live.threading.Thread
    class Unstartable:
        def __init__(self, **kwargs):
            pass
        def start(self):
            raise RuntimeError('synthetic thread exhaustion')
    monkeypatch.setattr(live.threading, 'Thread', Unstartable)
    tick(p)
    assert runtime._snapshot['runtime']['observer_errors']['radio_inventory'] == 'RuntimeError'
    assert runtime._radio_inventory_inflight is False
    assert runtime.process is child
    monkeypatch.setattr(live.threading, 'Thread', real_thread)
    runtime.close()
    assert runtime._lock.closed


def test_log_close_failure_does_not_abandon_runtime_lock(platform):
    p, runtime = platform, platform.runtime
    start(p)
    real_log = runtime.log_handle
    real_log.close()
    class BrokenLog:
        def close(self):
            raise OSError('flush failed')
    runtime.log_handle = BrokenLog()
    runtime.close()
    assert runtime._lock.closed
    assert runtime.process is None
    assert 'close failed' in runtime.log_error


@pytest.mark.parametrize('totals', [None, [], {'error': -1}, {'error': True}, pytest.param({'error': 10**5000}, id='oversized')])
def test_malformed_processing_totals_do_not_crash_or_turn_green(platform, totals):
    from redux.display.capture_panel import model as capture_model
    p, runtime = platform, platform.runtime
    start(p)
    child = runtime.process
    p.worker['by_status'] = totals
    tick(p, 6)
    assert area(runtime._snapshot, 'capture processing')['status'] == 'degraded'
    view = capture_model(runtime._snapshot, now=p.clock[0])
    if runtime._snapshot['capture_processing'].get('available') is True:
        assert view['ledger'] == view['scan_state'] == 'INVALID'
    else:
        assert view['ledger'] == 'UNAVAILABLE'
    json.dumps(runtime._snapshot, allow_nan=False)
    assert runtime.process is child


def test_invalid_sampler_candidate_does_not_poison_previous_measurement(platform, monkeypatch):
    p, runtime = platform, platform.runtime
    start(p)
    previous = runtime.resources.snapshot
    sample = p.collector.sample
    monkeypatch.setattr(p.collector, 'sample', lambda: SimpleNamespace(reading=object(), measured_at=p.clock[0]))
    tick(p, 3)
    assert runtime.resources.snapshot is previous
    assert runtime._snapshot['telemetry']['fresh'] is False
    assert area(runtime._snapshot, 'thermal/power')['status'] == 'unknown'
    monkeypatch.setattr(p.collector, 'sample', sample)
    tick(p, 3)
    assert runtime.resources.snapshot is not previous
    assert runtime._snapshot['telemetry']['fresh'] is True


@pytest.mark.parametrize('adapter', ['telemetry_status', 'governor_status'])
def test_broken_augur_adapter_cannot_drop_canonical_resource_state(platform, monkeypatch, adapter):
    p, runtime = platform, platform.runtime
    start(p)
    child = runtime.process
    monkeypatch.setattr(runtime.augur, adapter, lambda: (_ for _ in ()).throw(RuntimeError('adapter unavailable')))
    tick(p, 6)
    assert runtime.process is child
    assert runtime._snapshot['telemetry']['values']['cpu_temp_c'] == 45
    assert runtime._snapshot['governor']['mode'] == 'full'
    assert runtime._snapshot['ui']['sensors']['state'] == 'fresh'
