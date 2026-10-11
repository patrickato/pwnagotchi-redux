"""Build host preflight exercises read-only outcomes, no network or mounts."""
from pathlib import Path
import importlib.util
import subprocess

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'image/host_preflight.py'
SPEC = importlib.util.spec_from_file_location('redux_host_preflight', SOURCE)
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def fake_runner(argv, **kwargs):
    if argv[0] == 'arch-test':
        return subprocess.CompletedProcess(argv, 0, 'arm64\n', '')
    if argv[0] == 'findmnt':
        return subprocess.CompletedProcess(argv, 0, 'ext4\n', '')
    raise AssertionError('Unexpected subprocess: ' + repr(argv))


def kwargs(tmp_path):
    return dict(workspace=tmp_path/'fresh',uid=0,system='Linux',machine='x86_64',
                which=lambda k: '/usr/bin/'+k,runner=fake_runner,
                fs_type='ext4',available_bytes=40*p.GIB)


def test_healthy_host_indicates_prerequisites_not_a_build(tmp_path):
    result=p.report(**kwargs(tmp_path))
    assert result['overall']=='host_checks_ok'
    assert result['build_ready'] is True
    assert result['image_built'] is False
    assert result['physical_boot_verified'] is False
    assert result['network_sources_fetched'] is False


def test_missing_tools_are_named_and_block(tmp_path):
    kw=kwargs(tmp_path)
    kw['which']=lambda name: None if name in {'quilt','qemu-aarch64-static'} else '/usr/bin/'+name
    report=p.report(**kw)
    assert report['overall']=='blocked'
    assert report['checks']['dependencies']['evidence']['missing']==['quilt','qemu-aarch64-static']


def test_native_arm64_does_not_require_qemu_binary(tmp_path):
    kw=kwargs(tmp_path)
    kw['machine']='aarch64'
    kw['which']=lambda name: None if name=='qemu-aarch64-static' else '/usr/bin/'+name
    assert p.report(**kw)['checks']['dependencies']['status']=='ok'


def test_unprivileged_is_blocked_but_probe_never_mutates(tmp_path):
    kw=kwargs(tmp_path)
    kw['uid']=1000
    r=p.report(**kw)
    assert 'privilege' in r['blockers']
    assert not kw['workspace'].exists()


@pytest.mark.parametrize('fs', ['overlay','9p','ntfs','tmpfs','btrfs'])
def test_non_ext4_is_not_accepted(tmp_path, fs):
    kw=kwargs(tmp_path)
    kw['fs_type']=fs
    assert 'workspace_filesystem' in p.report(**kw)['blockers']


def test_existing_build_path_refused(tmp_path):
    kw=kwargs(tmp_path)
    kw['workspace'].mkdir()
    r=p.report(**kw)
    assert 'workspace_path' in r['blockers']


def test_space_shortage_blocks_and_marginal_space_warns(tmp_path):
    kw=kwargs(tmp_path)
    kw['available_bytes']=10*p.GIB
    assert 'disk_space' in p.report(**kw)['blockers']
    kw['available_bytes']=28*p.GIB
    result=p.report(**kw)
    assert result['checks']['disk_space']['status']=='advisory'
    assert result['build_ready']


def test_arm64_execution_rejected_on_failed_arch_test(tmp_path):
    kw=kwargs(tmp_path)
    kw['runner']=lambda argv,**_: subprocess.CompletedProcess(argv,1,'','')
    assert p.report(**kw)['checks']['arm64_execution']['status']=='blocker'


def test_unobservable_filesystem_never_claims_ready(tmp_path):
    kw=kwargs(tmp_path)
    kw['fs_type']=''
    result=p.report(**kw)
    assert result['overall']=='unverified'
    assert result['build_ready'] is False


def test_linux_only_and_path_without_whitespace(tmp_path):
    kw=kwargs(tmp_path)
    kw['system']='Windows'
    kw['workspace']=tmp_path/'space path'
    r=p.report(**kw)
    assert 'platform' in r['blockers']
    assert 'workspace_path' in r['blockers']


def test_report_serializes_and_has_explicit_limited_claims(tmp_path):
    import json
    r=p.report(**kwargs(tmp_path))
    s=json.dumps(r)
    assert 'network_sources_fetched' in s
    assert 'physical_boot_verified' in s


def test_check_host_is_wired_to_build_script_without_fetch_or_root(tmp_path):
    import os
    script = SOURCE.parent.parent / "build.sh"
    assert script.exists()
    source = script.read_text()
    assert '--check-host)' in source
    assert source.index('--check-host)') < source.index('if [[ $PREPARE_ONLY == 0 ]]; then')
    assert source.index('host_preflight.py" --workspace "$BUILD_DIR"') < source.index('checkout_source "$PI_GEN_URL"')
    result = subprocess.run(
        ["bash", str(script), "--check-host"], text=True, capture_output=True,
        env={**os.environ, "REDUX_BUILD_DIR":str(tmp_path / "fresh")}, timeout=15)
    assert result.returncode in (0, 2)
    import json
    report=json.loads(result.stdout)
    assert report["image_built"] is False
    assert report["network_sources_fetched"] is False
    assert not (tmp_path/"fresh").exists()
