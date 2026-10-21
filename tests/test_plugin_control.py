"""Data-only plugin control acceptance; no installed code execution."""
import hashlib
import json
from pathlib import Path
import pytest
from redux.packs import PackManager, DependencyError, load_manifest
from redux.packs.control import compatibility, validate_options
from redux.packs.status import InventoryProvider


def pack(root, name='sample', **extra):
    directory = root / name
    directory.mkdir(exist_ok=True)
    (directory / 'pack.json').write_text(json.dumps({'name': name, **extra}))
    return directory


def test_preview_commit_cold_boot_rollback(tmp_path):
    pack(tmp_path)
    manager = PackManager(tmp_path)
    plan = manager.preview('enable', 'sample')
    assert not manager.is_enabled('sample')
    manager.apply(plan['revision'], 'enable', 'sample')
    assert PackManager(tmp_path).is_enabled('sample')
    plan = manager.preview('disable', 'sample')
    manager.apply(plan['revision'], 'disable', 'sample')
    plan = manager.preview('rollback', 'sample')
    manager.apply(plan['revision'], 'rollback', 'sample')
    assert manager.is_enabled('sample')
    assert manager.inventory()['plugins'][0]['active'] is None


def test_failed_write_preserves_state(tmp_path, monkeypatch):
    pack(tmp_path)
    manager = PackManager(tmp_path)
    plan = manager.preview('enable', 'sample')
    def fail(*args):
        raise OSError('interrupted storage')
    monkeypatch.setattr(manager, '_atomic', fail)
    with pytest.raises(OSError):
        manager.apply(plan['revision'], 'enable', 'sample')
    assert not manager.is_enabled('sample')
    assert not PackManager(tmp_path).is_enabled('sample')


def test_stale_preview_and_source_revision(tmp_path):
    directory = pack(tmp_path)
    manager = PackManager(tmp_path)
    plan = manager.preview('enable', 'sample')
    (directory / 'pack.json').write_text('{"name":"sample","description":"changed"}')
    with pytest.raises(ValueError, match='stale'):
        manager.apply(plan['revision'], 'enable', 'sample')


@pytest.mark.parametrize('extra', [{'requires': ['missing']}, {'requires': ['sample']}, {'conflicts': ['sample']}, {'side_effects': True}, {'requirements': ['pi5']}])
def test_reject_unsafe_request(tmp_path, extra):
    pack(tmp_path, **extra)
    with pytest.raises(DependencyError):
        PackManager(tmp_path).preview('enable', 'sample')


def test_explicit_side_effect_request_never_activates(tmp_path):
    pack(tmp_path, side_effects=True)
    manager = PackManager(tmp_path)
    plan = manager.preview('enable', 'sample', authorize_side_effects=True)
    manager.apply(plan['revision'], 'enable', 'sample', authorize_side_effects=True)
    assert manager.inventory()['plugins'][0]['active'] is None


@pytest.mark.parametrize('schema,value', [({'type':'boolean'}, 'yes'), ({'type':'number'}, float('nan')), ({'type':'number','min':0}, -1), ({'type':'enum','choices':['a']}, 'b'), ({'type':'string','max_length':2}, 'long'), ({'type':'path'}, '/etc/passwd'), ({'type':'path'}, '/captures/plugins/../secret')])
def test_typed_configuration_rejections(schema, value):
    with pytest.raises(ValueError):
        validate_options({'value':schema}, {'value':value})


def test_secret_redaction_unknown_preservation(tmp_path):
    pack(tmp_path, options={'token': {'type':'string','sensitive':True,'default':'PRIVATE'}})
    state = tmp_path / '.packs-state.json'
    state.write_text(json.dumps({'legacy': {'private':'keep'}, 'configuration': {'sample': {'old':'retain'}}}))
    manager = PackManager(tmp_path)
    plan = manager.preview('configure', 'sample', {'token':'SECRET'})
    manager.apply(plan['revision'], 'configure', 'sample', {'token':'SECRET'})
    assert 'SECRET' not in json.dumps(manager.inventory())
    assert 'PRIVATE' not in json.dumps(manager.inventory())
    saved = json.loads(state.read_text())
    assert saved['legacy']['private'] == 'keep'
    assert saved['configuration']['sample']['old'] == 'retain'
    assert state.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('body', ['[]', '{', '{"name":"../escape"}', '{"name":"bad","requires":"oops"}', 'x' * 65537])
def test_bad_manifests_are_isolated(tmp_path, body):
    directory = pack(tmp_path, 'bad')
    (directory / 'pack.json').write_text(body)
    pack(tmp_path, 'good')
    manager = PackManager(tmp_path)
    assert [item.name for item in manager.list()] == ['good']
    assert manager.errors


@pytest.mark.parametrize('source,expected', [('def on_loaded(self): pass', 'adapted'), ('def on_unrecognized(self): pass', 'incompatible'), ('def on_ready(self, agent): agent.reboot()', 'incompatible'), ('def on_ready(self): pass', 'incompatible')])
def test_static_adapter_never_imports(tmp_path, source, expected):
    source = 'from redux.compat import Plugin\nraise RuntimeError("must not execute")\nclass Adapted(Plugin):\n    ' + source
    directory = pack(tmp_path, adapter='pwnagotchi-shim-v1', entrypoint='plugin.py', sha256=hashlib.sha256(source.encode()).hexdigest())
    (directory / 'plugin.py').write_text(source)
    assert compatibility(load_manifest(directory / 'pack.json'))['state'] == expected


def test_symlink_and_corrupt_state_refused(tmp_path):
    directory = pack(tmp_path)
    manifest = directory / 'pack.json'
    manifest.unlink()
    manifest.symlink_to('/etc/passwd')
    assert PackManager(tmp_path).errors
    state = tmp_path / '.packs-state.json'
    state.write_text('{')
    with pytest.raises(ValueError):
        PackManager(tmp_path)


def test_inventory_cache_failure_recovery(tmp_path):
    pack(tmp_path)
    clock = [0]
    provider = InventoryProvider(tmp_path, clock=lambda: clock[0])
    assert provider()['availability'] == 'inspected'
    (tmp_path / '.packs-state.json').write_text('{')
    assert provider()['availability'] == 'inspected'
    clock[0] = 11
    assert provider()['availability'] == 'error'
    (tmp_path / '.packs-state.json').unlink()
    clock[0] = 22
    assert provider()['availability'] == 'inspected'


def test_multiple_transactions_and_external_stale_preview(tmp_path):
    pack(tmp_path)
    first = PackManager(tmp_path)
    plan = first.preview('enable', 'sample')
    second = PackManager(tmp_path)
    second.enable('sample')
    with pytest.raises(ValueError, match='stale'):
        first.apply(plan['revision'], 'enable', 'sample')
    for _ in range(5):
        second.disable('sample')
        second.enable('sample')
    assert PackManager(tmp_path).is_enabled('sample')


def test_invalid_unknown_options_and_path_schema(tmp_path):
    pack(tmp_path)
    with pytest.raises(ValueError):
        PackManager(tmp_path).preview('configure', 'sample', {'unknown': True})
    directory = pack(tmp_path, 'unsafe', options={'path': {'type':'path', 'root':'/'}})
    with pytest.raises(ValueError):
        load_manifest(directory / 'pack.json')


def test_web_readonly_authorization_and_escaped_manifest(tmp_path, monkeypatch):
    import threading
    import urllib.request
    import urllib.error
    from http.server import ThreadingHTTPServer
    from redux.web.status_page import make_handler
    pack(tmp_path, description='<script>alert(1)</script>')
    monkeypatch.setenv('REDUX_PACKS_DIR', str(tmp_path))
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(lambda: {}, token='local'))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + '/api/packs')
        assert error.value.code == 401
        request = urllib.request.Request(base + '/plugins', headers={'Authorization':'Bearer local'})
        body = urllib.request.urlopen(request).read().decode()
        assert 'sample' in body and 'active runtime' in body
        assert '<script>alert(1)</script>' not in body
        request = urllib.request.Request(base + '/api/packs', method='POST', data=b'{}', headers={'Authorization':'Bearer local'})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 501
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_large_catalog_pagination(tmp_path):
    for index in range(55):
        pack(tmp_path, f'plugin{index:03}')
    provider = InventoryProvider(tmp_path)
    first = provider()
    second = provider(50)
    assert len(first['plugins']) == 50
    assert first['next_offset'] == 50
    assert len(second['plugins']) == 5
    assert second['next_offset'] is None
    assert not ({p['name'] for p in first['plugins']} & {p['name'] for p in second['plugins']})


def test_cli_preview_confirm_configure_and_redaction(tmp_path, capsys):
    from redux.cli import main
    pack(tmp_path, options={'token': {'type':'string', 'sensitive':True}})
    prefix = ['packs', '--dir', str(tmp_path)]
    assert main(prefix + ['enable', 'sample']) == 0
    preview = json.loads(capsys.readouterr().out)
    assert not PackManager(tmp_path).is_enabled('sample')
    assert main(prefix + ['enable', 'sample', '--confirm', preview['revision']]) == 0
    capsys.readouterr()
    assert main(prefix + ['configure', 'sample', '--options', '{"token":"SECRET"}']) == 0
    assert 'SECRET' not in capsys.readouterr().out
    assert main(prefix + ['list']) == 0
    assert 'SECRET' not in capsys.readouterr().out


def test_framebuffer_system_details_show_unknown_activation(tmp_path):
    from redux.display.ui_state import model
    from redux.display.fbui import render
    pack(tmp_path)
    state = {'plugins': InventoryProvider(tmp_path)()}
    rows = model(state, now=100)['rows']
    row = next(r for r in rows if r['label'] == 'Plugin sample')
    assert 'active unknown' in row['value']
    image = render(state, page='system', now=100, system_offset=max(0, rows.index(row) - 1))
    assert image.size == (480, 320)


def test_duplicate_names_are_unavailable(tmp_path):
    pack(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'pack.json').write_text('{"name":"sample"}')
    manager = PackManager(tmp_path)
    assert manager.get('sample') is None
    assert manager.errors


def test_interruption_after_backup_preserves_last_requested_state(tmp_path, monkeypatch):
    pack(tmp_path)
    manager = PackManager(tmp_path)
    manager.enable('sample')
    plan = manager.preview('disable', 'sample')
    atomic = manager._atomic
    def interrupted(path, data):
        if path == manager._state_path:
            raise OSError('interrupted before replace')
        atomic(path, data)
    monkeypatch.setattr(manager, '_atomic', interrupted)
    with pytest.raises(OSError):
        manager.apply(plan['revision'], 'disable', 'sample')
    assert PackManager(tmp_path).is_enabled('sample')
    assert json.loads((tmp_path / '.packs-state.json.previous').read_text())['enabled'] == ['sample']


def test_required_configuration_and_missing_catalog(tmp_path):
    pack(tmp_path, options={'interval': {'type':'number', 'required':True, 'min':1, 'max':60}})
    manager = PackManager(tmp_path)
    with pytest.raises(ValueError, match='required'):
        manager.preview('enable', 'sample')
    plan = manager.preview('configure', 'sample', {'interval':30})
    manager.apply(plan['revision'], 'configure', 'sample', {'interval':30})
    manager.enable('sample')
    assert InventoryProvider(tmp_path / 'absent')()['availability'] == 'unavailable'


@pytest.mark.parametrize('spec', [{'type':'number', 'min':'wrong'}, {'type':'number','min':10,'max':1}, {'type':'enum', 'choices':{}}, {'type':'boolean','sensitive':'yes'}])
def test_malformed_option_schemas_isolated(tmp_path, spec):
    directory = pack(tmp_path, options={'option':spec})
    with pytest.raises(ValueError):
        load_manifest(directory / 'pack.json')


def test_unsupported_legacy_is_not_imported(tmp_path):
    (tmp_path / 'legacy.py').write_text('raise RuntimeError("untrusted")')
    inventory = PackManager(tmp_path).inventory()
    assert inventory['errors'][0]['state'] == 'incompatible'
    assert not inventory['plugins']


def test_inventory_missing_dependencies_and_invalid_pagination(tmp_path):
    pack(tmp_path, requires=['absent'])
    manager = PackManager(tmp_path)
    assert 'Missing dependencies' in manager.inventory()['plugins'][0]['diagnostics'][0]
    with pytest.raises(ValueError):
        manager.inventory(offset=-1)


def test_discovery_budget_explicitly_blocks_requests(tmp_path, monkeypatch):
    from types import SimpleNamespace
    pack(tmp_path)
    times = iter([0, 1])
    monkeypatch.setattr('redux.packs.manager.time', SimpleNamespace(monotonic=lambda: next(times)))
    manager = PackManager(tmp_path)
    assert manager.inventory()['partial']
    with pytest.raises(DependencyError, match='incomplete'):
        manager.preview('enable', 'sample')


def test_disappearing_pack_and_stale_active_claim_remain_unknown(tmp_path):
    pack(tmp_path)
    state = tmp_path / '.packs-state.json'
    state.write_text(json.dumps({'enabled':['sample', 'disappeared'], 'active': {'sample':True}, 'updated_at':1}))
    inventory = InventoryProvider(tmp_path)()
    records = {item['name']: item for item in inventory['plugins']}
    assert records['sample']['active'] is None
    assert records['disappeared']['state'] == 'unavailable'
    assert records['disappeared']['active'] is None
    assert json.loads(state.read_text())['active']['sample'] is True
