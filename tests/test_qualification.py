"""Qualification orchestration uses fake processes only; never SSH or a Pi."""
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize('result,expected', [([], 0), ([('x','UNKNOWN','missing')],3), ([('x','FAIL','bad')],1)])
def test_exit_codes(result, expected):
    assert module('redux-qualify').summarize(result) == expected


def test_default_is_offline_uses_existing_runners_once_and_isolates_cache(monkeypatch):
    q = module('redux-qualify')
    monkeypatch.setattr(q, 'load', lambda name: SimpleNamespace(checks=lambda: [('tools','PASS','ready')]))
    calls, prefixes = [], []
    def runner(command, **kwargs):
        assert command[0] != 'ssh'
        calls.append(command)
        prefixes.append(kwargs['env']['PYTHONPYCACHEPREFIX'])
        assert Path(prefixes[-1]).parent.is_dir()
        kwargs['stdout'].write(b'42 passed\n')
        return SimpleNamespace(returncode=0)
    assert q.main([], runner=runner) == 0
    assert len(calls) == 5
    assert sum(command[-1] == 'image/ci.sh' for command in calls) == 1
    assert sum('pytest' in command for command in calls) == 1
    assert not Path(prefixes[0]).parent.exists()


def test_failure_not_masked_other_checks_continue(monkeypatch, capsys):
    q = module('redux-qualify')
    monkeypatch.setattr(q, 'load', lambda name: SimpleNamespace(checks=lambda: []))
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        kwargs['stdout'].write(('failure π\n' * 1000).encode())
        return SimpleNamespace(returncode=7 if len(calls) == 1 else 0)
    assert q.main([], runner=runner) == 1
    assert len(calls) == 5
    assert 'exit 7' in capsys.readouterr().err


def test_missing_dependency_prevents_silent_success(monkeypatch):
    q = module('redux-qualify')
    monkeypatch.setattr(q, 'load', lambda name: SimpleNamespace(checks=lambda: [('pytest','FAIL','absent')]))
    def forbidden(*args, **kwargs):
        raise AssertionError('process should not run')
    assert q.main([], runner=forbidden) == 1


def test_field_uses_one_unprivileged_probe_and_acceptance_policy(monkeypatch, tmp_path):
    q = module('redux-qualify')
    original = q.load
    monkeypatch.setattr(q, 'load', lambda name: SimpleNamespace(checks=lambda: []) if name == 'dev-preflight' else original(name))
    key = tmp_path / 'key'
    key.touch()
    calls = []
    def runner(command, **kwargs):
        assert command[0] == 'ssh'
        calls.append(command)
        assert 'StrictHostKeyChecking=yes' in command
        assert 'PreferredAuthentications=publickey' in command
        assert 'PasswordAuthentication=no' in command
        assert 'sudo' not in command
        assert 'redux-report' not in ' '.join(command)
        assert ' -B ' in command[-1]
        return subprocess.CompletedProcess(command, 0, '{"doctor_overall":"unknown","areas":{},"telemetry":null,"acceptance":{}}','')
    assert q.main(['--preflight-only','--field','--host','192.168.1.2','--key',str(key)], runner=runner) == 3
    assert len(calls) == 1


@pytest.mark.parametrize('host', ['127.0.0.1','0.0.0.0','8.8.8.8','192.168.1.2; echo bad'])
def test_bad_field_target_cannot_execute(monkeypatch, tmp_path, host):
    q = module('redux-qualify')
    original = q.load
    monkeypatch.setattr(q, 'load', lambda name: SimpleNamespace(checks=lambda: []) if name == 'dev-preflight' else original(name))
    key = tmp_path / 'key'
    key.touch()
    def forbidden(*args, **kwargs):
        raise AssertionError('SSH attempted')
    assert q.main(['--preflight-only','--field','--host',host,'--key',str(key)], runner=forbidden) == 2


def test_timeout_returns_failure():
    q = module('redux-qualify')
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('fixture', 1)
    assert q.run_gate('tests', ['test'], {}, runner=timeout)[1] == 'FAIL'


def test_preflight_detects_tools_without_installing(monkeypatch):
    p = module('dev-preflight')
    monkeypatch.setenv('CC', "'")
    checks = p.checks(finder=lambda name: None, which=lambda name: None,
                      runner=lambda *args, **kw: (_ for _ in ()).throw(AssertionError('unexpected execution')))
    assert next(state for name,state,_ in checks if name == 'pytest') == 'FAIL'
    assert next(state for name,state,_ in checks if name == 'host C compiler') == 'FAIL'
    assert not any(state == 'PASS' for name,state,_ in checks if name != 'Python >= 3.11')


def test_unprivileged_acceptance_does_not_infer_systemd_storage_or_monitor_mode():
    gate = module('field-acceptance')
    checks = {name: state for name,state,_ in gate.status_verdicts({
        'live_state':'running','checkpoint_freshness':'fresh','dashboard':'True',
        'capture_link_type':'803','events_seen':'5','handed_off':'1'}, 'US')}
    assert checks['Monitor packet link type'] == 'PASS'
    assert checks['REDUXCAP ext4'] == checks['redux.service'] == checks['Monitor radio'] == 'UNKNOWN'
    stale = {name:state for name,state,_ in gate.status_verdicts({'live_state':'running','checkpoint_freshness':'stale'})}
    assert stale['Live capture supervisor'] == 'UNKNOWN'
    assert stale['Live checkpoint freshness'] == 'FAIL'


def test_test_cache_is_owned_and_outside_repo():
    import os
    import sys
    prefix = Path(sys.pycache_prefix)
    assert prefix.is_dir() and prefix.stat().st_uid == os.getuid()
    assert ROOT not in prefix.parents
    assert os.environ['PYTHONPYCACHEPREFIX'] == str(prefix)
