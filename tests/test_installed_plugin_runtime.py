"""Extracted-root integration using real staged catalog and installed sources.

No Pi, chroot, services, radio operations or physical display certification.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from redux.packs.locations import locations, mount_guard, prepare_parent, CATALOG, STATE
from redux.packs.manager import PackManager
from redux.packs.status import InventoryProvider

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def installed(tmp_path):
    root=tmp_path/'root';root.mkdir()
    # Image stages install this exact catalog; use the real copy operation with
    # the stage60 directory, without starting or emulating privileged services.
    catalog=root/'usr/share/redux/packs'
    shutil.copytree(ROOT/'image/stage-redux/60-packs/files/catalog',catalog)
    shutil.copytree(ROOT/'redux',root/'opt/redux/redux',ignore=shutil.ignore_patterns('__pycache__'))
    return root,catalog,root/'captures/config/packs.json'


def test_shared_defaults_and_explicit_development_override(monkeypatch,tmp_path):
    monkeypatch.delenv('REDUX_PACKS_DIR',raising=False);monkeypatch.delenv('REDUX_PACKS_STATE',raising=False)
    assert locations()==(CATALOG,STATE)
    assert locations(tmp_path)==(tmp_path,tmp_path/'.packs-state.json')
    monkeypatch.setenv('REDUX_PACKS_DIR',str(tmp_path))
    assert locations()==locations(tmp_path)


def test_installed_cli_cold_boot_request_and_upgrade_preserves_state(installed):
    root,catalog,state=installed
    env={**os.environ,'PYTHONPATH':str(root/'opt/redux'),'REDUX_PACKS_DIR':str(catalog),'REDUX_PACKS_STATE':str(state),'PYTHONDONTWRITEBYTECODE':'1'}
    def cli(*args):
        result=subprocess.run([sys.executable,'-B','-m','redux.cli','packs',*args],cwd=root,env=env,text=True,capture_output=True,timeout=10)
        assert result.returncode==0,result.stderr
        return json.loads(result.stdout)
    inventory=cli('list')
    assert inventory['plugins'][0]['name']=='field-diagnostics'
    assert not inventory['plugins'][0]['requested_enabled']
    assert not state.exists() and not state.parent.exists()
    plan=cli('enable','field-diagnostics')
    assert not state.exists()
    cli('enable','field-diagnostics','--confirm',plan['revision'])
    saved=state.read_bytes()
    assert state.stat().st_mode&0o777==0o600
    assert cli('list')['plugins'][0]['requested_enabled']
    assert cli('list')['plugins'][0]['active'] is None
    # Image upgrade replaces only immutable catalog/code, never user state.
    shutil.copytree(ROOT/'image/stage-redux/60-packs/files/catalog',catalog,dirs_exist_ok=True)
    assert state.read_bytes()==saved
    assert cli('list')['plugins'][0]['requested_enabled']


def test_no_radio_touch_telemetry_or_worker_required_for_inventory(installed):
    from redux.display.ui_state import model
    from redux.display.fbui import render
    root,catalog,state=installed
    report={'plugins':InventoryProvider(catalog,state)()}
    assert report['plugins']['availability']=='inspected'
    rows=model(report,now=100)['rows']
    assert any(row['label']=='Plugin field-diagnostics' and 'active unknown' in row['value'] for row in rows)
    assert render(report,page='system',now=100).size==(480,320)
    assert not state.exists()


def test_state_corruption_does_not_erase_or_disable_runtime_status(installed):
    root,catalog,state=installed
    state.parent.mkdir(parents=True);state.write_text('{corrupt')
    clock=[0];provider=InventoryProvider(catalog,state,clock=lambda:clock[0])
    assert provider()['availability']=='error'
    assert state.read_text()=='{corrupt'
    state.write_text('{"enabled":[]}');clock[0]=11
    assert provider()['availability']=='inspected'
    assert not provider()['plugins'][0]['requested_enabled']


def test_missing_capture_mount_never_creates_state_parent(tmp_path):
    state=Path('/captures/config/packs.json')
    with pytest.raises(ValueError,match='mount unavailable'):
        mount_guard(state,probe=lambda:None)
    assert not (tmp_path/'state').exists()


def test_mount_generation_loss_stops_commit_preserving_requests(installed,monkeypatch):
    root,catalog,state=installed
    token=[('mount1',)]
    guard=mount_guard('/captures/config/packs.json',probe=lambda:token[0])
    prepare_parent(state,guard)
    manager=PackManager(catalog,state,write_guard=guard)
    plan=manager.preview('enable','field-diagnostics')
    atomic=manager._atomic
    def interrupted(path,data):
        atomic(path,data)
        token[0]=None
    monkeypatch.setattr(manager,'_atomic',interrupted)
    with pytest.raises(ValueError,match='mount changed'):
        manager.apply(plan['revision'],'enable','field-diagnostics')
    assert not state.exists()
    assert not PackManager(catalog,state).is_enabled('field-diagnostics')


def test_state_write_failure_then_recovery(installed,monkeypatch):
    root,catalog,state=installed;state.parent.mkdir(parents=True)
    manager=PackManager(catalog,state)
    plan=manager.preview('enable','field-diagnostics')
    atomic=manager._atomic
    def fail(*args):raise OSError('storage unavailable')
    monkeypatch.setattr(manager,'_atomic',fail)
    with pytest.raises(OSError):manager.apply(plan['revision'],'enable','field-diagnostics')
    assert not state.exists() and not manager.is_enabled('field-diagnostics')
    monkeypatch.setattr(manager,'_atomic',atomic)
    manager.apply(plan['revision'],'enable','field-diagnostics')
    assert PackManager(catalog,state).is_enabled('field-diagnostics')


def test_image_export_requires_pack_code_catalog_and_runtime_dependencies(tmp_path):
    # Reuse the current root contract/fixture, not a second image validator.
    from test_root_payload import slot,gate
    root=slot(tmp_path)
    assert gate.verify(root)['root_payload_verified']
    catalog=root/'usr/share/redux/packs/field-diagnostics/pack.toml'
    original=catalog.read_text();catalog.write_text(original+'\n# changed\n')
    with pytest.raises(ValueError,match='reviewed'):gate.verify(root)
    catalog.write_text(original)
    packages=root/'usr/share/redux/packages.tsv'
    packages.write_text(packages.read_text().replace('python3-pil\t1.0\n',''))
    with pytest.raises(ValueError,match='dependency inventory'):gate.verify(root)


def test_stage60_installs_reviewed_catalog_and_captures_final_packages(tmp_path):
    stage=tmp_path/'stage';shutil.copytree(ROOT/'image/stage-redux/60-packs',stage)
    (stage/'files/boot').mkdir();shutil.copy2(ROOT/'boot/packs.py',stage/'files/boot/packs.py')
    root=tmp_path/'root';(root/'opt/redux/boot').mkdir(parents=True)
    program='on_chroot() { cat > "$ROOTFS_DIR/final-stage-command"; }; export -f on_chroot; bash ./00-run.sh'
    result=subprocess.run(['bash','-c',program],cwd=stage,env={**os.environ,'ROOTFS_DIR':str(root)},text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert (root/'usr/share/redux/packs/field-diagnostics/pack.toml').read_bytes()==(stage/'files/catalog/field-diagnostics/pack.toml').read_bytes()
    assert 'dpkg-query -W' in (root/'final-stage-command').read_text()
    assert 'dpkg-query -W' not in (ROOT/'image/stage-redux/00-redux/00-run.sh').read_text()
    assert 'python3-dbus-next' in (ROOT/'image/stage-redux/00-redux/00-packages-nr').read_text()


def test_authenticated_installed_api_and_tft_use_compact_bounded_state(installed,monkeypatch):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from redux.web.status_page import make_handler
    from redux.display.fbui import read_status
    from copy import deepcopy
    root,catalog,state=installed
    monkeypatch.setenv('REDUX_PACKS_DIR',str(catalog));monkeypatch.setenv('REDUX_PACKS_STATE',str(state))
    data={'doctor':{'overall':'unknown','findings':[]},'runtime':{'state':'starting'}}
    original=deepcopy(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(lambda:data,token='local'))
    thread=threading.Thread(target=server.serve_forever);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def get(path):
        return urllib.request.urlopen(urllib.request.Request(base+path,headers={'Authorization':'Bearer local'}))
    try:
        with get('/api/status') as response:payload=json.load(response)
        assert payload['plugins']['availability']=='inspected'
        assert 'options' not in payload['plugins']['plugins'][0]
        assert data==original
        with get('/api/packs') as response:full=json.load(response)
        assert full['plugins'][0]['options']=={}
        assert 'do not launch' in full['plugins'][0]['description']
        with get('/plugins') as response:assert 'field-diagnostics' in response.read().decode()
        assert not state.exists()
    finally:
        server.shutdown();thread.join();server.server_close()


def test_large_plugin_schema_cannot_overflow_native_live_status(installed):
    root,catalog,state=installed
    extra=catalog/'large';extra.mkdir()
    (extra/'pack.json').write_text(json.dumps({'name':'large','options':{
        'large_enum':{'type':'enum','choices':['x'*500+str(i) for i in range(100)]}}}))
    provider=InventoryProvider(catalog,state)
    assert len(json.dumps(provider()).encode())>40000
    assert len(json.dumps(provider.summary()).encode())<4096
    assert len(provider.pages)<=4
    for offset in range(10):provider(offset)
    assert len(provider.pages)==4


def test_mount_loss_invalidates_cached_inventory_without_rescan(installed):
    root,catalog,state=installed;alive=[True]
    def factory(path):
        def guard():
            if not alive[0]:raise ValueError('mount lost')
        return guard
    provider=InventoryProvider(catalog,state,guard_factory=factory)
    assert provider()['availability']=='inspected'
    alive[0]=False
    assert provider()['availability']=='error'
    assert not state.exists()


def test_plugin_error_visible_without_changing_doctor_or_runtime(installed):
    from redux.display.ui_state import model
    from redux.display.fbui import render
    report={'plugins':{'availability':'error'},'doctor':{'overall':'ok','findings':[]},'runtime':{}}
    rows=model(report,now=100)['rows']
    assert any(row['label']=='Plugin diagnostics' for row in rows)
    assert render(report,page='doctor',now=100).size==(480,320)
    assert report['doctor']['overall']=='ok'


def test_real_core_stage_installs_cli_services_configs_and_ui(tmp_path):
    stage=tmp_path/'stage';shutil.copytree(ROOT/'image/stage-redux/00-redux',stage)
    files=stage/'files'
    shutil.copytree(ROOT/'redux',files/'redux',ignore=shutil.ignore_patterns('__pycache__'))
    (files/'nexmon').mkdir()
    for source,target in [('image/sources.sh','sources.sh'),('systemd/redux-fbui.service','redux-fbui.service'),
                          ('config/fbui.example.env','fbui.example.env')]:
        shutil.copy2(ROOT/source,files/target)
    (files/'redux-revision').write_text('a'*40+'\n')
    pipeline=files/'pipeline';pipeline.mkdir()
    for source,target in [('config/live.example.toml','live.toml'),('config/pipeline.example.toml','pipeline.toml'),
                          ('scripts/redux-live-diag.sh','redux-live-diag.sh'),('scripts/redux-field-report.py','redux-field-report.py')]:
        shutil.copy2(ROOT/source,pipeline/target)
    for unit in ('redux-capture-ingest','redux-capture-audit'):
        for suffix in ('service','timer'):shutil.copy2(ROOT/f'systemd/{unit}.{suffix}',pipeline)
    root=tmp_path/'root';(root/'boot/firmware').mkdir(parents=True)
    program='on_chroot() { cat > "$ROOTFS_DIR/chroot-commands"; }; export -f on_chroot; bash ./00-run.sh'
    result=subprocess.run(['bash','-c',program],cwd=stage,env={**os.environ,'ROOTFS_DIR':str(root)},text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    # Actual staged launchers target the copied package, not source-checkout files.
    for command,module in (('redux','redux.cli'),('redux-fbui','redux.display.fbui'),
                           ('redux-display-diag','redux.display.acceptance')):
        launcher=root/'usr/local/bin'/command
        assert launcher.stat().st_mode&0o111
        assert f'-m {module}' in launcher.read_text()
        assert 'PYTHONPATH=/opt/redux' in launcher.read_text()
        assert (root/'opt/redux'/Path(module.replace('.','/')+'.py')).is_file()
    assert (root/'etc/redux/live.toml').read_bytes()==(ROOT/'config/live.example.toml').read_bytes()
    assert (root/'etc/redux/pipeline.toml').read_bytes()==(ROOT/'config/pipeline.example.toml').read_bytes()
    assert (root/'etc/systemd/system/redux-live.service').read_bytes()==(files/'redux-live.service').read_bytes()
    assert 'ReadWritePaths=/captures' in (root/'etc/systemd/system/redux-live.service').read_text()
    assert not (root/'captures/config/packs.json').exists()
    # Chroot/apt/systemctl are recorded, never executed by this test.
    assert 'systemctl enable redux-live.service' in (root/'chroot-commands').read_text()


def test_independent_plugin_provider_exception_cannot_remove_live_status(installed,monkeypatch):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    import redux.web.status_page as web
    class Broken:
        def summary(self):raise RuntimeError('PRIVATE producer error')
    monkeypatch.setattr('redux.packs.status.configured_provider',lambda:Broken())
    runtime={'state':'running','events_seen':7}
    server=ThreadingHTTPServer(('127.0.0.1',0),web.make_handler(lambda:{'runtime':runtime,'doctor':{'overall':'unknown','findings':[]}}))
    thread=threading.Thread(target=server.serve_forever);thread.start()
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/status') as response:payload=json.load(response)
        assert payload['runtime']==runtime
        assert payload['plugins']['availability']=='error'
        assert 'PRIVATE' not in json.dumps(payload)
    finally:
        server.shutdown();thread.join();server.server_close()


def test_installed_cli_typed_config_requests_preserve_prior_valid_state(installed):
    root,catalog,state=installed
    settings=catalog/'settings';settings.mkdir()
    (settings/'pack.json').write_text(json.dumps({'name':'settings','kind':'tool',
        'options':{'refresh':{'type':'number','min':1,'max':60}}}))
    env={**os.environ,'PYTHONPATH':str(root/'opt/redux'),'REDUX_PACKS_DIR':str(catalog),'REDUX_PACKS_STATE':str(state)}
    def cli(*args):
        return subprocess.run([sys.executable,'-B','-m','redux.cli','packs',*args],cwd=root,env=env,text=True,capture_output=True,timeout=10)
    preview=cli('configure','settings','--options','{"refresh":30}')
    assert preview.returncode==0,preview.stderr
    revision=json.loads(preview.stdout)['revision']
    result=cli('configure','settings','--options','{"refresh":30}','--confirm',revision)
    assert result.returncode==0,result.stderr
    saved=state.read_bytes()
    assert json.loads(saved)['configuration']['settings']['refresh']==30
    assert cli('configure','settings','--options','{"refresh":-1}').returncode==1
    assert state.read_bytes()==saved


def test_capture_label_must_match_the_actual_mounted_block_device(monkeypatch):
    import importlib
    from types import SimpleNamespace
    import stat
    module=importlib.import_module('redux.packs.locations')
    monkeypatch.setattr(module,'capture_mount_snapshot',lambda:('verified-generation',))
    device=[5]
    label=SimpleNamespace(is_symlink=lambda:True,stat=lambda:SimpleNamespace(st_mode=stat.S_IFBLK,st_rdev=device[0]))
    mount=SimpleNamespace(stat=lambda:SimpleNamespace(st_dev=5))
    monkeypatch.setattr(module,'Path',lambda value:label if value.startswith('/dev') else mount)
    assert module.captures_snapshot()==('verified-generation',)
    device[0]=6
    assert module.captures_snapshot() is None


def test_invalid_state_identifiers_remain_preserved_and_unavailable(installed):
    root,catalog,state=installed;state.parent.mkdir(parents=True)
    state.write_text(json.dumps({'enabled':['../escape']}))
    before=state.read_bytes()
    assert InventoryProvider(catalog,state)()['availability']=='error'
    assert state.read_bytes()==before


def test_long_manifest_labels_cannot_expand_compact_status(installed):
    root,catalog,state=installed
    for index in range(12):
        path=catalog/f'long{index}';path.mkdir()
        (path/'pack.json').write_text(json.dumps({'name':f'long{index}','version':'v'*2000}))
    summary=InventoryProvider(catalog,state).summary()
    assert len(json.dumps(summary).encode())<8192
    assert all(len(row['version'] or '')<=96 for row in summary['plugins'])
