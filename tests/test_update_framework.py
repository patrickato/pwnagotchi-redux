"""Read-only update/recovery gates: synthetic snapshots, never installed systems."""
from __future__ import annotations
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
# cryptography (with its cffi/_cffi_backend native dependency) can be absent or
# fail to import on some hosts/CI runners — and when its import raises anything
# other than a plain ModuleNotFoundError (e.g. a cffi ABI error), an unguarded
# top-level import crashes COLLECTION, which exits pytest with code 2 and takes
# the two modules that import from this one down with it. Catch any failure and
# skip the whole module cleanly instead.
try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except Exception as _crypto_error:  # noqa: BLE001 - any import-time failure must skip, not crash
    pytest.skip(f"cryptography unavailable or failed to import: {type(_crypto_error).__name__}",
                allow_module_level=True)

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'image'))
import update_safety as safety
from boot import ota

REV='a'*40
UUIDS={str(i):f'{i:08x}-1234-1234-1234-{i:012x}' for i in range(1,7)}
CONTEXT={'current':'A','devices':{n:'/dev/disk/by-partuuid/'+v for n,v in UUIDS.items()}}


def fixture_module(name):
    spec=importlib.util.spec_from_file_location('update_fixture_'+name,ROOT/'tests'/('test_'+name+'.py'))
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def put(root,name,data):
    path=root/name; path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(data if isinstance(data,bytes) else data.encode())
    return path


@pytest.fixture(scope='session')
def certificates(tmp_path_factory):
    folder=tmp_path_factory.mktemp('ephemeral-update-signing')
    result=[]
    for i in range(2):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Synthetic Redux test '+str(i))])
        cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
              .public_key(key.public_key()).serial_number(i+1)
              .not_valid_before(datetime(2020,1,1)).not_valid_after(datetime(2040,1,1))
              .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
              .sign(key,hashes.SHA256()))
        pem=put(folder,f'test-{i}.pem',cert.public_bytes(serialization.Encoding.PEM))
        private=put(folder,f'test-{i}.key',key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
        private.chmod(0o600)
        result.append((pem,private))
    return result


def configured_root(tmp_path,certificates,name='A'):
    root=fixture_module('root_payload').slot(tmp_path,name,profile='ab')
    put(root,'usr/share/redux/redux-revision',REV+'\n')
    put(root,'etc/redux/regulatory-country','US\n')
    put(root,'etc/rauc/keyring.pem',certificates[0][0].read_bytes())
    put(root,'usr/local/bin/redux-ota','#!/bin/sh\nexport PYTHONPATH=/opt/redux\nexec /usr/bin/python3 /opt/redux/boot/ota.py "$@"\n').chmod(0o755)
    generator=root/'usr/lib/systemd/system-generators/redux-ota'
    generator.parent.mkdir(parents=True,exist_ok=True)
    generator.symlink_to('/usr/local/libexec/redux-rauc-generator')
    put(root,'opt/redux/boot/ota.py',(ROOT/'boot/ota.py').read_bytes())
    for mode in ('backend','preinstall','health','deadline','generator'):
        put(root,'usr/local/libexec/redux-rauc-'+mode,
            '#!/bin/sh\nexport PYTHONPATH=/opt/redux\nexec /usr/bin/python3 /opt/redux/boot/ota.py '+mode+' "$@"\n').chmod(0o755)
    for name in ('redux-ota-health.service','redux-ota-deadline.service','redux-ota-deadline.timer'):
        put(root,'etc/systemd/system/'+name,(ROOT/'image/stage-redux/50-rauc/files'/name).read_bytes())
    put(root,'etc/systemd/system/rauc.service.d/redux.conf',(ROOT/'image/stage-redux/50-rauc/files/rauc.conf').read_bytes())
    put(root,'etc/systemd/system.conf.d/redux-watchdog.conf',(ROOT/'image/stage-redux/30-watchdog/files/watchdog.conf').read_bytes())
    put(root,'boot/firmware/config.txt','[all]\narm_64bit=1\nkernel_watchdog_timeout=60\n')
    return root


def saved_state(tmp_path, *, stable='A',current='A',pending=None):
    root=tmp_path/'snapshot'; root.mkdir()
    put(root,'boot/redux-control/autoboot.txt',ota.autoboot(stable))
    put(root,'proc/cmdline','root=PARTUUID='+UUIDS[str(ota.SLOTS[current]['root'])]+' rauc.slot='+current)
    put(root,'run/redux-ota/context.json',json.dumps(dict(CONTEXT,current=current)))
    state=ota.initial_state(stable)
    if pending is not None:
        state['pending']=pending;state['states'][pending]='good'
    put(root,'captures/rauc/boot-state.json',json.dumps(state))
    return root


def test_configured_is_not_automatic_rollback_or_install_permission(tmp_path,certificates):
    root=configured_root(tmp_path,certificates)
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file() and not p.is_symlink()}
    result=safety.inspect_configuration(root,revision=REV,context=CONTEXT)
    assert result['condition']=='configured' and result['result']=='UNKNOWN'
    assert not result['automatic_rollback_verified'] and not result['update_permitted']
    assert all(p.read_bytes()==data for p,data in before.items())


def test_masked_missing_config_is_explicitly_unconfigured(tmp_path):
    root=fixture_module('root_payload').slot(tmp_path)
    put(root,'usr/share/redux/redux-revision',REV)
    result=safety.inspect_configuration(root,revision=REV)
    assert result['condition']=='unconfigured' and result['result']=='UNKNOWN'


@pytest.mark.parametrize('mutate',[
    lambda s:s.replace('bootloader=custom','bootloader=uboot'),
    lambda s:s.replace('parent=rootfs.0','parent=rootfs.1'),
    lambda s:s.replace('bundle-formats=verity','bundle-formats=plain verity'),
    lambda s:s.replace('statusfile=/captures/rauc/status.raucs','statusfile=/etc/status'),
    lambda s:s.replace('type=ext4','type=raw',1),
    lambda s:s.replace('bootname=A','bootname=B'),
    lambda s:s.replace('[slot.boot.1]','[slot.captures.1]'),
    lambda s:s+'\n[slot.data.0]\ndevice=/dev/foo\ntype=ext4\n',
    lambda s:s+'\n[DEFAULT]\nbundle-formats=plain\n',
])
def test_unsafe_or_unsupported_slot_config_fails(mutate):
    with pytest.raises((ValueError,NotImplementedError)):
        safety.validate_configuration(mutate(ota.system_conf(CONTEXT)),CONTEXT)


@pytest.mark.parametrize('filename,value',[
    ('etc/rauc/keyring.pem',b'PRIVATE KEY'),
    ('usr/share/redux/redux-revision',b'b'*40),
    ('usr/local/libexec/redux-rauc-backend',b'#!/bin/sh\nreboot\n'),
    ('etc/systemd/system.conf.d/redux-watchdog.conf',b'[Manager]\nRuntimeWatchdogSec=0\n'),
    ('boot/firmware/config.txt',b'arm_64bit=1\n'),
    ('etc/systemd/system/redux-ota-deadline.service',b'[Service]\nRestart=no\n'),
])
def test_readiness_rejects_modified_required_inputs(tmp_path,certificates,filename,value):
    root=configured_root(tmp_path,certificates)
    put(root,filename,value)
    assert safety.inspect_configuration(root,revision=REV,context=CONTEXT)['result']=='FAIL'


def test_other_backend_condition_and_missing_context(tmp_path,certificates):
    root=configured_root(tmp_path,certificates)
    config=root/'etc/rauc/system.conf';config.unlink()
    put(root,'etc/rauc/system.conf',ota.system_conf(CONTEXT).replace('bootloader=custom','bootloader=grub'))
    assert safety.inspect_configuration(root,revision=REV,context=CONTEXT)['condition']=='unsupported'
    config.unlink();config.symlink_to('/run/redux-ota/system.conf')
    assert safety.inspect_configuration(root,revision=REV)['condition']=='unverified'


def test_keyring_expiry_private_material_and_malformed_pem(certificates):
    with pytest.raises(ValueError): safety.public_keyring(certificates[0][0].read_bytes(),now=datetime(2050,1,1,tzinfo=timezone.utc))
    with pytest.raises(ValueError): safety.public_keyring(b'not a certificate')
    with pytest.raises(ValueError): safety.public_keyring(certificates[0][1].read_bytes())


@pytest.mark.parametrize('name',['../outside','a/../b','a/./b','/etc/passwd','a//b'])
def test_snapshot_paths_do_not_escape(tmp_path,name):
    with pytest.raises(ValueError): safety.read(tmp_path,name)


def test_parent_and_leaf_symlinks_are_not_read(tmp_path):
    (tmp_path/'linked').symlink_to('/etc')
    with pytest.raises(ValueError): safety.read(tmp_path,'linked/passwd')
    (tmp_path/'leaf').symlink_to('/etc/passwd')
    with pytest.raises(ValueError): safety.read(tmp_path,'leaf')


@pytest.mark.parametrize('stable,current,pending,trial,phase',[
    ('A','A',None,None,'stable'),('A','A','B',False,'staged-or-fallback'),
    ('A','B','B',True,'candidate-trial'),('B','B','B',True,'interrupted-commit'),
    ('A','B','B',None,'blocked'),('A','B',None,True,'blocked'),
])
def test_recovery_plans_no_mutation(tmp_path,stable,current,pending,trial,phase):
    root=saved_state(tmp_path,stable=stable,current=current,pending=pending)
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result=safety.recovery_plan(root,trial=trial)
    assert result['phase']==phase and not result['execution_permitted']
    assert not result['automatic_rollback_verified']
    assert {p:p.read_bytes() for p in before}==before


@pytest.mark.parametrize('missing',['captures/rauc/boot-state.json','run/redux-ota/context.json','boot/redux-control/autoboot.txt'])
def test_missing_recovery_evidence_cannot_be_a_factory_success(tmp_path,missing):
    root=saved_state(tmp_path)
    (root/missing).unlink()
    assert safety.recovery_plan(root)['phase']=='blocked'


def test_duplicate_and_contradictory_recovery_ledger(tmp_path):
    root=saved_state(tmp_path)
    put(root,'captures/rauc/boot-state.json','{"schema":1,"schema":1}')
    assert safety.recovery_plan(root)['phase']=='blocked'
    put(root,'captures/rauc/boot-state.json',json.dumps({'schema':1,'pending':None,'states':{'A':'bad','B':'good'}}))
    assert safety.recovery_plan(root)['phase']=='blocked'


def valid_manifest():
    return '[update]\ncompatible='+ota.COMPATIBLE+'\nversion=test\nbuild='+REV+'\n[bundle]\nformat=verity\n[hooks]\nfilename=hook.sh\n[image.boot]\nfilename=boot.tar\nhooks=post-install;\n[image.rootfs]\nfilename=rootfs.tar\n'


@pytest.mark.parametrize('mutation',[
    lambda s:s.replace('boot.tar','../boot.tar'),lambda s:s.replace('rootfs.tar','/etc/shadow'),
    lambda s:s.replace('hook.sh','../../hook.sh'),lambda s:s+'\n[handlers]\ninstall=/bin/sh\n',
    lambda s:s.replace('hooks=post-install;','hooks=post-install;install-check;'),
    lambda s:s+'\n[DEFAULT]\nfilename=override\n',lambda s:s.replace('format=verity','format=plain'),
    lambda s:s.replace(ota.COMPATIBLE,'another-board'),lambda s:s.replace('[image.rootfs]','[image.data]'),
])
def test_bundle_manifest_policy_rejects_unsafe_extensions(mutation):
    with pytest.raises(ValueError): ota.validate_manifest(mutation(valid_manifest()))


def test_normalized_rauc_hook_delimiter_is_supported():
    assert ota.validate_manifest(valid_manifest()).get('bundle','format')=='verity'


def test_cli_unknown_and_fail_exit_codes(tmp_path):
    command=[sys.executable,str(ROOT/'scripts/redux-release.py'),'--json','recovery','--root',str(tmp_path)]
    result=subprocess.run(command,capture_output=True,text=True,check=False)
    assert result.returncode==1 and json.loads(result.stdout)['phase']=='blocked'


@pytest.mark.parametrize('payload',[
    '{"schema":1,"schema":1,"pending":null,"states":{"A":"good","B":"bad"}}',
    '{"schema":1,"pending":null,"states":{"A":[],"B":"bad"}}',
    '{"schema":1,"pending":null,"states":{"A":"good","B":"bad"},"extra":NaN}',
])
def test_runtime_ledger_fails_closed_without_type_crashes(tmp_path,payload):
    path=put(tmp_path,'state.json',payload)
    with pytest.raises(ValueError):ota.load_state(path,'A')


@pytest.mark.parametrize('field,alter',[
    ('build',lambda s:s.replace('build='+REV,'build=main')),
    ('version',lambda s:s.replace('version=test','version=../../candidate')),
])
def test_signed_build_identity_and_version_required(field,alter):
    with pytest.raises(ValueError):ota.validate_manifest(alter(valid_manifest()))


@pytest.mark.parametrize('name',[
    'usr/lib/systemd/system-generators/redux-ota',
    'etc/systemd/system/timers.target.wants/redux-ota-deadline.timer',
    'usr/local/libexec/redux-rauc-health',
])
def test_missing_lifecycle_registration_fails_readiness(tmp_path,certificates,name):
    root=configured_root(tmp_path,certificates);(root/name).unlink()
    assert safety.inspect_configuration(root,revision=REV,context=CONTEXT)['result']=='FAIL'


def test_manifest_parser_size_and_controls_are_bounded():
    for value in ('x'*65537,'\x00',None):
        with pytest.raises(ValueError):ota.validate_manifest(value)


def test_cli_reports_configured_but_physical_unknown(tmp_path,certificates):
    root=configured_root(tmp_path,certificates)
    context=put(tmp_path,'context.json',json.dumps(CONTEXT))
    result=subprocess.run([sys.executable,str(ROOT/'scripts/redux-release.py'),'--json','rauc',
        '--root',str(root),'--revision',REV,'--context',str(context)],capture_output=True,text=True,check=False)
    assert result.returncode==3
    record=json.loads(result.stdout)
    assert record['condition']=='configured' and not record['automatic_rollback_verified']


def test_deadline_retry_rate_limit_belongs_to_unit_not_service():
    config=safety.ini((ROOT/'image/stage-redux/50-rauc/files/redux-ota-deadline.service').read_text())
    assert config.get('Unit','StartLimitIntervalSec')=='0'
    assert not config.has_option('Service','StartLimitIntervalSec')


def test_private_snapshots_reject_links_and_nonregular_files(tmp_path):
    import os
    source=put(tmp_path,'artifact',b'content')
    linked=tmp_path/'link';linked.symlink_to(source)
    with pytest.raises(ValueError):safety.snapshot_file(linked,tmp_path/'copy')
    hard=tmp_path/'hard';os.link(source,hard)
    with pytest.raises(ValueError):safety.snapshot_file(hard,tmp_path/'copy')
    hard.unlink()
    fifo=tmp_path/'fifo';os.mkfifo(fifo)
    with pytest.raises(ValueError):safety.snapshot_file(fifo,tmp_path/'copy')
    assert not (tmp_path/'copy').exists()
    assert source.read_bytes()==b'content'
