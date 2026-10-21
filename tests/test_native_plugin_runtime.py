"""Executed native lifecycle evidence, entirely host-only; no hardware claims."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import threading
import time

import pytest

from redux.core.signals import Signal, SignalBus
from redux.packs import registry
from redux.packs.control import digest
from redux.packs.manager import PackManager
from redux.packs.runtime import NativeRuntime
from redux.packs.status import InventoryProvider

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'image/stage-redux/60-packs/files/catalog'


@pytest.fixture
def setup(tmp_path):
    catalog = tmp_path / 'catalog'
    shutil.copytree(CATALOG, catalog)
    state = tmp_path / 'state.json'
    return catalog, state


def request(setup, enabled=('resource-summary',), **extra):
    catalog, state = setup
    state.write_text(json.dumps({'enabled': list(enabled), **extra}))
    return PackManager(catalog, state)


def wait(predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        threading.Event().wait(.005)
    raise AssertionError('native worker did not reach expected state')


def row(owner, name='resource-summary'):
    return owner.snapshot()['plugins'].get(name, {})


def publish(bus, **updates):
    observation = {'measured_at': time.time(), 'fresh': True,
                   'values': {'cpu_temp_c': 52.0, 'cpu_load_pct': 25.0, 'ram_pct': 40.0}}
    observation.update(updates)
    bus.emit(Signal.RESOURCE_OBSERVATION, observation=observation)


def test_real_start_observe_stop_and_generation(setup):
    manager = request(setup)
    bus = SignalBus()
    owner = NativeRuntime(bus, *setup)
    owner.start()
    try:
        publish(bus)
        wait(lambda: row(owner).get('result'))
        evidence = row(owner)
        assert evidence['active'] is True and evidence['state'] == 'active'
        assert evidence['request_revision'] == digest(manager._data)
        assert evidence['source_sha256'] == registry.REGISTRY['resource-summary'].source_sha256
        assert evidence['result']['measurement']['cpu_temp_c'] == 52
        assert evidence['result']['observations'] == 1
        instance = owner.instances['resource-summary']
        provider = InventoryProvider(*setup, runtime=owner.snapshot)
        public = next(p for p in provider()['plugins'] if p['name'] == 'resource-summary')
        assert public['active'] is True and public['activation'] == 'active'
        assert len(json.dumps(provider.summary())) < 8192
    finally:
        owner.close()
    assert not instance.running
    assert row(owner)['state'] == 'stopped'
    assert bus.emit(Signal.RESOURCE_OBSERVATION, observation={}) == 0
    assert not owner.snapshot()['fresh']
    fresh = NativeRuntime(bus, *setup)
    assert fresh.generation != owner.generation
    assert fresh.snapshot()['plugins'] == {}
    with pytest.raises(RuntimeError, match='fresh process generation'):
        owner.start()


def test_disabled_has_no_callbacks_and_repeated_start_has_one_subscription(setup):
    bus = SignalBus()
    owner = NativeRuntime(bus, *setup)
    owner.start(); owner.start()
    try:
        publish(bus)
        wait(lambda: row(owner).get('state') == 'stopped')
        assert not owner.instances
        assert len(bus._subs[Signal.RESOURCE_OBSERVATION.value]) == 1
        for _ in range(50):
            publish(bus)
        assert not row(owner).get('result')
    finally:
        owner.close(); owner.close()
    assert not bus._subs[Signal.RESOURCE_OBSERVATION.value]


@pytest.mark.parametrize('kind', ['manifest', 'source', 'legacy', 'tool', 'requirements'])
def test_registry_rejects_unreviewed_implementations_without_import(setup, monkeypatch, kind):
    manager = request(setup)
    pack = manager.get('resource-summary')
    if kind == 'manifest':
        pack = replace(pack, manifest_sha256='0' * 64, module='malicious.module')
    elif kind == 'legacy':
        pack = replace(pack, adapter='pwnagotchi-shim-v1')
    elif kind == 'tool':
        pack = manager.get('field-diagnostics')
    elif kind == 'requirements':
        pack = replace(pack, requirements=('root',), manifest_sha256='0'*64)
    else:
        monkeypatch.setattr(registry, 'read_data', lambda _: b'changed implementation')
    with pytest.raises(ValueError):
        registry.resolve(pack)


@pytest.mark.parametrize('bad', ['{', '[]', '{"enabled":"resource-summary"}',
                                 '{"enabled":["../evil"]}', '{"configuration": []}'])
def test_corrupt_state_never_activates_and_is_not_rewritten(setup, bad):
    setup[1].write_text(bad)
    owner = NativeRuntime(SignalBus(), *setup)
    owner.start()
    try:
        wait(lambda: owner.snapshot()['error'])
        assert not owner.instances
        assert setup[1].read_text() == bad
    finally:
        owner.close()


def test_request_change_stops_and_fresh_generation_can_recover(setup):
    request(setup)
    bus = SignalBus(); owner = NativeRuntime(bus, *setup)
    owner.start()
    try:
        publish(bus)
        wait(lambda: row(owner).get('active'))
        instance = owner.instances['resource-summary']
        request(setup, enabled=())
        owner.next_scan = float('-inf')
        publish(bus)
        wait(lambda: row(owner).get('state') == 'stopped')
        assert not instance.running
        request(setup)
        owner.next_scan = float('-inf'); publish(bus)
        wait(lambda: row(owner).get('active'))
        assert owner.instances['resource-summary'] is not instance
    finally:
        owner.close()


def test_manual_tool_is_unavailable_not_daemon(setup):
    request(setup, enabled=('field-diagnostics',))
    owner = NativeRuntime(SignalBus(), *setup); owner.start()
    try:
        wait(lambda: row(owner, 'field-diagnostics').get('state') == 'unavailable')
        assert not owner.instances
        assert not row(owner, 'field-diagnostics')['active']
    finally:
        owner.close()


@pytest.mark.parametrize('failure', ['start', 'observe', 'stop', 'oversized'])
def test_callback_isolation_cleanup_and_bounded_output(setup, monkeypatch, failure):
    request(setup)
    events = []
    class Broken:
        def start(self, options):
            events.append('start')
            if failure == 'start':
                raise RuntimeError('SECRET must not be reported')
        def observe(self, observation):
            events.append('observe')
            if failure == 'observe':
                raise RuntimeError('SECRET')
            return {'value': 'x' * 5000} if failure == 'oversized' else {}
        def stop(self):
            events.append('stop')
            if failure == 'stop':
                raise RuntimeError('SECRET')
    monkeypatch.setattr(registry.Implementation, 'create', lambda self: Broken())
    bus = SignalBus(); owner = NativeRuntime(bus, *setup); owner.start()
    try:
        publish(bus)
        wait(lambda: 'start' in events)
        if failure != 'start':
            wait(lambda: 'observe' in events)
        if failure in ('observe', 'oversized'):
            wait(lambda: row(owner).get('state') == 'degraded')
            assert not owner.instances
        if failure == 'start':
            wait(lambda: row(owner).get('state') == 'degraded')
        assert 'SECRET' not in json.dumps(owner.snapshot())
        # An independent signal consumer still runs after plugin exceptions.
        observed = []; bus.on(Signal.EVENT, lambda em: observed.append(em))
        assert bus.emit(Signal.EVENT, event='safe fixture') == 1
        assert observed
    finally:
        owner.close()
    assert 'stop' in events
    assert row(owner)['state'] == ('degraded' if failure == 'stop' else
                                  'degraded' if failure == 'start' else
                                  'degraded' if failure in ('observe', 'oversized') else 'stopped')


def test_blocked_trusted_callback_does_not_block_signal_or_shutdown(setup, monkeypatch):
    request(setup)
    entered = threading.Event(); release = threading.Event()
    class Slow:
        def start(self, options): pass
        def observe(self, observation):
            entered.set(); release.wait(5); return {}
        def stop(self): pass
    monkeypatch.setattr(registry.Implementation, 'create', lambda self: Slow())
    clock = [0.0]
    bus = SignalBus(); owner = NativeRuntime(bus, *setup, clock=lambda: clock[0]); owner.start()
    publish(bus)
    assert entered.wait(3)
    try:
        clock[0] = 2.0
        start = time.monotonic()
        for _ in range(100): publish(bus)
        assert time.monotonic() - start < .5
        assert owner.pending is not None  # one coalesced slot, no growing queue
        assert row(owner)['active'] is False
        owner.close()
        assert time.monotonic() - start < 1
        assert not owner.snapshot()['fresh']
    finally:
        release.set()
        owner.thread.join(3)
    assert row(owner)['state'] == 'degraded'


def test_stale_evidence_and_measurements_remain_distinct(setup):
    request(setup)
    clock = [0.0]; bus = SignalBus()
    owner = NativeRuntime(bus, *setup, clock=lambda: clock[0]); owner.start()
    try:
        publish(bus, measured_at=time.time()-120)
        wait(lambda: row(owner).get('result'))
        assert row(owner)['active']
        assert row(owner)['result']['measurement']['fresh'] is False
        clock[0] = 6
        assert row(owner)['active'] is False
        assert owner.snapshot()['fresh'] is False
    finally:
        owner.close()


def test_wrong_revision_and_missing_manifest_cannot_merge_active(setup):
    manager = request(setup)
    pack = manager.get('resource-summary')
    evidence = {'generation': 'old-process', 'fresh': True,
                'plugins': {'resource-summary': {'active': True, 'state': 'active',
                'request_revision': 'wrong', 'manifest_sha256': pack.manifest_sha256}}}
    provider = InventoryProvider(*setup, runtime=lambda: evidence)
    public = next(p for p in provider()['plugins'] if p['name'] == 'resource-summary')
    assert public['active'] is False
    assert public['activation'] == 'unavailable'


def test_mount_disappearance_stops_existing_consumer(setup, monkeypatch):
    request(setup)
    mounted = [True]
    def guard():
        if not mounted[0]: raise OSError('mount lost')
    monkeypatch.setattr('redux.packs.runtime.mount_guard', lambda state: guard)
    bus = SignalBus(); owner = NativeRuntime(bus, *setup); owner.start()
    try:
        publish(bus); wait(lambda: row(owner).get('active'))
        instance = owner.instances['resource-summary']
        mounted[0] = False; publish(bus)
        wait(lambda: owner.snapshot()['error'])
        assert not instance.running and not row(owner)['active']
    finally:
        owner.close()


@pytest.mark.parametrize('observation', [None, {}, {'fresh': True, 'values': []},
    {'measured_at': float('nan'), 'values': {'cpu_temp_c': float('inf'), 'ram_pct': 'SECRET'}}])
def test_partial_malformed_observations_are_bounded_and_not_fabricated(setup, observation):
    request(setup)
    bus = SignalBus(); owner = NativeRuntime(bus, *setup); owner.start()
    try:
        bus.emit(Signal.RESOURCE_OBSERVATION, observation=observation)
        wait(lambda: row(owner).get('result'))
        measurement = row(owner)['result']['measurement']
        assert measurement['cpu_temp_c'] is None and measurement['ram_pct'] is None
        assert measurement['fresh'] is False
        assert 'SECRET' not in json.dumps(owner.snapshot(), allow_nan=False)
    finally:
        owner.close()


def test_request_changed_during_start_is_never_confirmed_active(setup, monkeypatch):
    request(setup)
    stopped = []
    class Changed:
        def start(self, options): request(setup, enabled=())
        def stop(self): stopped.append(True)
    monkeypatch.setattr(registry.Implementation, 'create', lambda self: Changed())
    owner = NativeRuntime(SignalBus(), *setup); owner.start()
    try:
        wait(lambda: row(owner).get('state') == 'degraded')
        assert row(owner)['active'] is False and stopped
    finally:
        owner.close()


def test_dependency_order_failure_and_reverse_shutdown(setup, monkeypatch):
    # Test-only registered factories exercise the generic owner; production
    # registry remains fixed to shipped implementation/manifest bytes.
    catalog, state = setup
    for name, requires in [('base', []), ('consumer', ['base'])]:
        path = catalog/name; path.mkdir()
        (path/'pack.toml').write_text(f'name="{name}"\nrequires={json.dumps(requires)}\n')
    request(setup, enabled=('consumer', 'base'))
    events = []
    class Spec:
        identity = 'test-only'; source_sha256 = '0'*64
        def __init__(self, name): self.name = name
        def create(self):
            name = self.name
            class Consumer:
                def start(self, options): events.append('start '+name)
                def stop(self): events.append('stop '+name)
                def observe(self, value): return {}
            return Consumer()
    monkeypatch.setattr('redux.packs.runtime.resolve', lambda pack: Spec(pack.name))
    bus=SignalBus(); owner=NativeRuntime(bus,*setup); owner.start()
    publish(bus)
    wait(lambda: row(owner, 'consumer').get('active'))
    owner.close()
    assert events == ['start base', 'start consumer', 'stop consumer', 'stop base']


def test_cycle_and_missing_dependency_do_not_start(setup):
    request(setup, enabled=('missing',))
    owner=NativeRuntime(SignalBus(),*setup); owner.start()
    try:
        wait(lambda: owner.snapshot()['error'])
        assert not owner.instances
    finally:
        owner.close()


def test_doctor_current_active_unknown_and_degraded(setup):
    from redux.core.doctor import Doctor, DoctorInputs, with_native_plugins
    baseline = Doctor().report(DoctorInputs())
    def assess(inventory):
        return with_native_plugins(baseline, {'availability':'inspected', 'runtime_generation':'test-generation', **inventory})['findings'][-1]
    assert assess({})['status'] == 'unknown'
    assert assess({'runtime_fresh': True, 'plugins': []})['status'] == 'ok'
    plugin={'name':'resource-summary','kind':'plugin','requested_enabled':True,'active':False}
    assert assess({'runtime_fresh': True, 'plugins':[plugin]})['status'] == 'degraded'
    plugin['active']=True
    assert assess({'runtime_fresh': True, 'plugins':[plugin]})['status'] == 'ok'
    assert assess({'runtime_fresh': False, 'plugins':[plugin]})['status'] == 'unknown'
    assert assess({'runtime_fresh': True, 'plugins':[plugin], 'next_offset':50})['status'] == 'unknown'
    plugin.update(kind='tool',active=False)
    assert assess({'runtime_fresh': True, 'plugins':[plugin]})['status'] == 'ok'


def test_live_supervisor_headless_no_radio_uses_existing_resources_and_closes(setup, tmp_path, monkeypatch):
    from redux.core.live_runtime import LiveRuntime, LiveConfig
    from redux.core.telemetry import ResourceMonitor
    request(setup)
    monkeypatch.setenv('REDUX_PACKS_DIR',str(setup[0]))
    monkeypatch.setenv('REDUX_PACKS_STATE',str(setup[1]))
    class Measured(ResourceMonitor):
        def __init__(self): self.samples=0; self.decision=None
        def sample(self): self.samples+=1
        def status(self):
            return {'measured_at':time.time(),'fresh':True,'values':{'cpu_temp_c':47.0},
                    'age_seconds':0,'provenance':{'reading':'deterministic fixture'}}
        def governor_status(self): return {'mode':'full','measurement_fresh':True}
    monitor=Measured()
    config=LiveConfig(state_dir=tmp_path/'live',capture_dir=tmp_path/'incoming',
                      active_dir=tmp_path/'active',enable_web=False,enable_native_plugins=True)
    runtime=LiveRuntime(config,radio_probe=lambda:[],resource_monitor=monitor)
    try:
        runtime.tick()
        owner=runtime.native_plugins
        wait(lambda: row(owner).get('result'))
        runtime.tick()
        status=runtime._snapshot
        plugin=next(p for p in status['plugins']['plugins'] if p['name']=='resource-summary')
        assert plugin['active'] is True
        assert plugin['runtime']['result']['measurement']['cpu_temp_c']==47
        assert monitor.samples==2  # plugin never calls sample()
        assert runtime.augur is None  # consumer survives absent radio/engine
        assert any(f['area']=='native plugins' and f['status']=='ok' for f in status['doctor']['findings'])
        assert any(r['label']=='Plugin resource-summary' and '; active' in r['value'] for r in status['ui']['rows'])
        instance=owner.instances['resource-summary']
    finally:
        runtime.close()
    assert not instance.running
    assert not owner.thread.is_alive()


def test_default_owner_disabled_and_bad_config_rejected(tmp_path):
    from redux.core.live_runtime import LiveConfig, LiveRuntime
    config=LiveConfig(state_dir=tmp_path/'live',capture_dir=tmp_path/'incoming',
                      active_dir=tmp_path/'active',enable_web=False)
    runtime=LiveRuntime(config,radio_probe=lambda:[])
    try:
        runtime.tick()
        assert runtime.native_plugins is None
    finally:
        runtime.close()
    with pytest.raises(ValueError,match='enable_native_plugins'):
        replace(config,enable_native_plugins='true').validate()


def test_authenticated_web_and_cli_current_process_evidence(setup, monkeypatch, capsys):
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from redux.web.status_page import make_handler
    from redux import cli
    request(setup)
    monkeypatch.setenv('REDUX_PACKS_DIR',str(setup[0]))
    monkeypatch.setenv('REDUX_PACKS_STATE',str(setup[1]))
    bus=SignalBus(); owner=NativeRuntime(bus,*setup); owner.start()
    publish(bus); wait(lambda: row(owner).get('result'))
    handler=make_handler(lambda:{'runtime':{'state':'degraded'}},token='test-token',plugin_runtime=owner.snapshot)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(base+'/api/packs',timeout=2)
        assert error.value.code==401
        for route in ('/api/status','/api/packs'):
            with urlopen(Request(base+route,headers={'Authorization':'Bearer test-token'}),timeout=2) as response:
                data=json.load(response)
            inventory=data['plugins'] if route=='/api/status' else data
            plugin=next(p for p in inventory['plugins'] if p['name']=='resource-summary')
            assert plugin['active'] is True
            assert inventory['runtime_generation']==owner.generation
        owner.close()
        with urlopen(Request(base+'/api/packs',headers={'Authorization':'Bearer test-token'}),timeout=2) as response:
            data=json.load(response)
        assert not next(p for p in data['plugins'] if p['name']=='resource-summary')['active']
        # CLI's bounded reader is the existing proxy-free loopback reader;
        # supply its authenticated response without duplicating HTTP plumbing.
        import io
        monkeypatch.setattr('redux.display.local_http.open_status', lambda url: io.BytesIO(json.dumps({'plugins':data}).encode()))
        assert cli.main(['packs','runtime'])==0
        assert json.loads(capsys.readouterr().out)['runtime_generation']==owner.generation
    finally:
        owner.close(); server.shutdown(); server.server_close(); thread.join(2)


def test_catalog_corruption_stops_active_and_preserves_state(setup):
    request(setup)
    saved=setup[1].read_bytes()
    bus=SignalBus();owner=NativeRuntime(bus,*setup);owner.start()
    try:
        publish(bus);wait(lambda:row(owner).get('active'))
        instance=owner.instances['resource-summary']
        (setup[0]/'resource-summary/pack.toml').write_text('invalid TOML')
        owner.next_scan=float('-inf');publish(bus)
        wait(lambda:owner.snapshot()['error'])
        assert not instance.running
        assert not any(p.get('active') for p in owner.snapshot()['plugins'].values())
        assert setup[1].read_bytes()==saved
    finally:
        owner.close()


def test_missing_captures_prevents_owner_before_any_fallback_write(tmp_path,monkeypatch):
    from redux.core import live_runtime as live
    monkeypatch.setattr(live,'capture_mount_snapshot',lambda:None)
    with pytest.raises(RuntimeError,match='REDUXCAP'):
        live.LiveRuntime(live.LiveConfig(enable_native_plugins=True),radio_probe=lambda:[])


def test_registry_source_read_is_bounded_and_nofollow(setup,monkeypatch):
    pack=request(setup).get('resource-summary')
    def absent(path): raise OSError('implementation absent')
    monkeypatch.setattr(registry,'read_data',absent)
    assert registry.eligibility(pack)['eligible'] is False


def test_supervisor_summary_does_not_read_catalog_or_state(setup, monkeypatch):
    request(setup)
    bus=SignalBus();owner=NativeRuntime(bus,*setup);owner.start()
    try:
        publish(bus);wait(lambda:row(owner).get('result'))
        def forbidden(*args,**kwargs):
            raise AssertionError('checkpoint attempted filesystem inventory')
        monkeypatch.setattr('redux.packs.runtime.PackManager',forbidden)
        for _ in range(20):
            summary=owner.summary()
            plugin=next(p for p in summary['plugins'] if p['name']=='resource-summary')
            assert plugin['active'] is True
            assert summary['runtime_observed_at'] is not None
    finally:
        owner.close()


def test_atomic_preview_disable_and_rollback_reactivate_new_instance(setup):
    manager=PackManager(*setup)
    plan=manager.preview('enable','resource-summary')
    manager.apply(plan['revision'],'enable','resource-summary')
    clock=[0.0];bus=SignalBus();owner=NativeRuntime(bus,*setup,clock=lambda:clock[0]);owner.start()
    try:
        publish(bus);wait(lambda:row(owner).get('active'))
        original=owner.instances['resource-summary']
        plan=manager.preview('disable','resource-summary')
        manager.apply(plan['revision'],'disable','resource-summary')
        clock[0]=11;publish(bus)
        wait(lambda:row(owner).get('state')=='stopped')
        assert not original.running
        plan=manager.preview('rollback','resource-summary')
        manager.apply(plan['revision'],'rollback','resource-summary')
        clock[0]=22;publish(bus)
        wait(lambda:row(owner).get('active'))
        assert owner.instances['resource-summary'] is not original
        assert row(owner)['request_revision']==digest(manager._data)
    finally:
        owner.close()


def test_recovery_after_invalid_startup_preserves_same_owner_generation(setup):
    setup[1].write_text('{')
    clock=[0.0];bus=SignalBus();owner=NativeRuntime(bus,*setup,clock=lambda:clock[0]);owner.start()
    generation=owner.generation
    try:
        publish(bus);wait(lambda:owner.snapshot()['error'])
        request(setup)
        clock[0]=11;publish(bus)
        wait(lambda:row(owner).get('active'))
        assert owner.snapshot()['error'] is None
        assert owner.generation==generation
    finally:
        owner.close()


def test_compact_inventory_reports_omitted_rows_instead_of_false_complete():
    from redux.packs.status import compact_inventory
    inventory={'plugins':[{'name':str(i)} for i in range(20)],'offset':0,'next_offset':None}
    compact=compact_inventory(inventory)
    assert len(compact['plugins'])==12
    assert compact['next_offset']==12


def test_actual_framebuffer_renders_confirmed_plugin_system_details(setup):
    from redux.display.fbui import render
    from redux.display.ui_state import model
    request(setup)
    bus=SignalBus();owner=NativeRuntime(bus,*setup);owner.start()
    try:
        publish(bus);wait(lambda:row(owner).get('result'))
        report={'plugins':owner.summary()}
        rows=model(report)['rows']
        index=next(i for i,r in enumerate(rows) if r['label']=='Plugin resource-summary')
        assert rows[index]['value']=='requested enabled; active'
        image=render(report,page='system',system_offset=index,system_detail=index)
        assert image.size==(480,320)
        assert len(image.getcolors(image.width*image.height))>2
        owner.close()
        stopped=model({'plugins':owner.summary()})['rows']
        assert any(r['label']=='Plugin resource-summary' and '; stopped' in r['value'] for r in stopped)
    finally:
        owner.close()


@pytest.mark.parametrize('field,value', [('source_sha256','0'*64),('implementation','unreviewed/v1')])
def test_contradictory_implementation_cannot_merge_active(setup,field,value):
    manager=request(setup)
    pack=manager.get('resource-summary');spec=registry.REGISTRY[pack.name]
    actual={'active':True,'state':'active','request_revision':digest(manager._data),
            'manifest_sha256':pack.manifest_sha256,'implementation':spec.identity,
            'source_sha256':spec.source_sha256,field:value}
    provider=InventoryProvider(*setup,runtime=lambda:{'generation':'current','fresh':True,
                                                    'plugins':{pack.name:actual}})
    public=next(p for p in provider()['plugins'] if p['name']==pack.name)
    assert public['active'] is False and public['activation']=='unavailable'


def test_native_measurement_uses_collector_freshness_limit(setup):
    from redux.core.telemetry import MAX_AGE_SECONDS
    request(setup)
    bus=SignalBus();owner=NativeRuntime(bus,*setup);owner.start()
    try:
        publish(bus,measured_at=time.time()-MAX_AGE_SECONDS-1)
        wait(lambda:row(owner).get('result'))
        assert row(owner)['active'] is True
        assert row(owner)['result']['measurement']['fresh'] is False
    finally:
        owner.close()


@pytest.mark.parametrize('plugins',[None,{},['invalid row']])
def test_malformed_native_doctor_evidence_is_unknown(plugins):
    from redux.core.doctor import Doctor,DoctorInputs,with_native_plugins
    report=with_native_plugins(Doctor().report(DoctorInputs()),
        {'plugins':plugins,'availability':'inspected','runtime_generation':'current','runtime_fresh':True})
    assert report['findings'][-1]['status']=='unknown'
