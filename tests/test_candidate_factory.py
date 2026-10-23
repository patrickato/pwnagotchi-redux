"""Host factory transactions, fake builder execution and actual synthetic images.

Synthetic kernels, source repositories and ephemeral keys are TEST evidence only.
No root, mounts, actual pi-gen build, Wi-Fi, Pi or production signer is used.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'image'))
import candidate_factory as factory
# test_release_candidate (and test_update_framework behind it) skip at module
# level when cryptography can't be imported; keep that a clean skip here instead
# of a collection crash (pytest exit code 2).
try:
    from test_release_candidate import fixtures, certificates, add_boot, fat, ext, disk, requires_tools
except pytest.skip.Exception:
    pytest.skip("cryptography-backed signing fixtures unavailable", allow_module_level=True)
from release_manifest import publish


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repository(folder):
    root = folder/'repo'; root.mkdir()
    (root/'image').mkdir()
    (root/'image/sources.sh').write_text('PI_GEN_URL=https://example.org/pi-gen.git\nPI_GEN_REV='+ 'b'*40+'\nNEXMON_URL=https://example.org/nexmon.git\nNEXMON_REV='+ 'c'*40+'\n')
    (root/'image/config').write_text('IMG_NAME=synthetic\n')
    (root/'build.sh').write_text('#!/bin/sh\nexit 99 # never execute a real builder in these fixtures\n')
    command(['git','init','-q',str(root)])
    command(['git','-C',str(root),'add','.'])
    command(['git','-C',str(root),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','synthetic factory source'])
    return root, command(['git','-C',str(root),'rev-parse','HEAD'])


@pytest.fixture
def inputs(tmp_path,monkeypatch):
    root,revision=repository(tmp_path)
    monkeypatch.setattr(factory,'host_report',lambda p: {'build_ready':True,'blockers':[],'unverified':[]})
    monkeypatch.setattr(factory,'parser_run',lambda *a,**k:b'synthetic tool version')
    monkeypatch.setattr(factory.shutil,'which',lambda tool:'/synthetic/'+tool)
    return root,dict(revision=revision,profile='lite',country='US',workspace=tmp_path/'attempt',output=tmp_path/'candidate')


def test_preflight_read_only_and_profile_environment_explicit(inputs,monkeypatch):
    root,args=inputs
    monkeypatch.setenv('REDUX_OTA_CERT','/ambient/untrusted')
    monkeypatch.setenv('BASH_ENV','/ambient/script')
    result=factory.preflight(root,**args)
    assert result['result']=='PASS'
    assert result['image_built'] is False
    assert not args['workspace'].exists() and not args['output'].exists()
    assert 'REDUX_OTA_CERT' not in factory.environment()
    assert 'BASH_ENV' not in factory.environment()
    assert result['upstream']['PI_GEN_REV']=='b'*40
    assert result['source']['tree']


@pytest.mark.parametrize('case',['dirty','untracked','revision','profile','country','capacity','existing_output','linked_parent','overlap'])
def test_bad_inputs_never_start_build(inputs,case,tmp_path):
    root,args=inputs
    if case=='dirty':(root/'build.sh').write_text('modified')
    elif case=='untracked':(root/'new').write_text('concurrent work')
    elif case=='revision':args['revision']='a'*40
    elif case=='profile':args['profile']='other'
    elif case=='country':args['country']='ZZ'
    elif case=='capacity':args['captures_mib']=1
    elif case=='existing_output':args['output'].mkdir();(args['output']/'keep').write_text('old artifact')
    elif case=='linked_parent':
        link=tmp_path/'linked';link.symlink_to(tmp_path,target_is_directory=True);args['output']=link/'new'
    elif case=='overlap':args['output']=args['workspace']/'inside'
    result=factory.build(root,**args,runner=lambda *a,**k:pytest.fail('builder executed'))
    assert result['result']=='FAIL'
    assert not args['workspace'].exists()
    if case=='existing_output':assert (args['output']/'keep').read_text()=='old artifact'


def test_missing_tools_or_host_privileges_are_explicit(inputs,monkeypatch):
    root,args=inputs
    monkeypatch.setattr(factory.shutil,'which',lambda _:None)
    monkeypatch.setattr(factory,'host_report',lambda p:{'build_ready':False,'blockers':['privilege','arm64_execution'],'unverified':[]})
    result=factory.preflight(root,**args)
    assert result['result']=='FAIL'
    assert any('privilege' in row['detail'] for row in result['checks'])


@pytest.mark.parametrize('interruption',[False,True])
def test_failed_or_interrupted_build_retains_journal_not_success(inputs,interruption):
    root,args=inputs
    def runner(command,**kwargs):
        if interruption:raise KeyboardInterrupt()
        raise ValueError('synthetic interrupted builder')
    if interruption:
        with pytest.raises(KeyboardInterrupt):factory.build(root,**args,runner=runner)
    else:
        assert factory.build(root,**args,runner=runner)['result']=='FAIL'
    record=json.loads((args['workspace']/'attempt.json').read_text())
    assert record['state']==('interrupted' if interruption else 'failed')
    assert record['factory_completed'] is False
    assert not args['output'].exists()
    assert factory.preflight(root,**args)['result']=='FAIL'  # never resume stale attempt


def test_ab_requires_external_matching_signer(inputs,certificates):
    root,args=inputs;args.update(profile='ab')
    assert factory.preflight(root,**args)['result']=='FAIL'
    cert,key=certificates[0]
    args.update(cert=cert,key=key,keyring=cert,version='test-only')
    assert factory.preflight(root,**args)['result']=='PASS'
    args['key']=certificates[1][1]
    assert factory.preflight(root,**args)['result']=='FAIL'
    args['key']=key
    old_mode=key.stat().st_mode
    try:
        key.chmod(0o644)
        assert factory.preflight(root,**args)['result']=='FAIL'
    finally:key.chmod(old_mode)


def test_parser_unknown_or_failed_gate_cannot_complete():
    for checks in ([],[{'name':'Independent artifact authenticity','status':'FAIL'}],
                   [{'name':'Embedded runtime contract','status':'UNKNOWN'}]):
        with pytest.raises(ValueError):factory.completed_validation({'checks':checks},'lite')


def test_real_command_failure_deadline_and_log_budget_are_bounded(tmp_path):
    for label,program,options in [('failure','raise SystemExit(2)',{}),('deadline','import time;time.sleep(5)',{'timeout':.05}),
                                  ('budget','print("x"*10000)',{'limit':100})]:
        with pytest.raises(ValueError):
            factory.execute([sys.executable,'-c',program],env=factory.environment(),cwd=tmp_path,log=tmp_path/(label+'.log'),**options)
        assert (tmp_path/(label+'.log')).stat().st_size<=10000


@pytest.fixture(scope='module')
def factory_artifacts(tmp_path_factory,certificates):
    folder=tmp_path_factory.mktemp('synthetic-factory')
    root,revision=repository(folder)
    artifacts={}
    mib=1024*1024
    for profile in ('lite','ab'):
        dest=folder/profile;dest.mkdir()
        if profile=='ab':
            a=fixtures.configured_root(dest,certificates,'A');b=fixtures.configured_root(dest,certificates,'B')
            for slot in (a,b):fixtures.put(slot,'usr/share/redux/redux-revision',revision+'\n')
            aboot=add_boot(a,'A');bboot=add_boot(b,'B');ctrl=a/'boot/redux-control'
            fixtures.put(ctrl,'autoboot.txt',fixtures.ota.autoboot('A'));fixtures.put(ctrl,'config.txt','[all]\narm_64bit=1\n');fixtures.put(ctrl,'start.elf',b'synthetic firmware selector')
            parts=[fat(dest,'ctrl',32*mib,'REDUXCTRL',ctrl),fat(dest,'boota',64*mib,'REDUXBOOTA',aboot),fat(dest,'bootb',64*mib,'REDUXBOOTB',bboot),
                   ext(dest,'roota',16*mib,'REDUXROOTA',a),ext(dest,'rootb',16*mib,'REDUXROOTB',b),ext(dest,'captures',448*mib,'REDUXCAP')]
        else:
            a=fixtures.fixture_module('root_payload').slot(dest)
            fixtures.put(a,'usr/share/redux/redux-revision',revision+'\n');fixtures.put(a,'etc/redux/regulatory-country','US\n')
            aboot=add_boot(a)
            parts=[fat(dest,'boot',64*mib,'BOOT',aboot),ext(dest,'root',16*mib,'ROOTFS',a),ext(dest,'captures',448*mib,'REDUXCAP')]
        image,_=disk(dest,parts,profile)
        publish(image,revision,'b'*40,'c'*40,expected_profile=profile)
        artifacts[profile]=(image,a,aboot)
    return folder,root,revision,artifacts


def fake_builder(artifacts,profile,certificates,*,change=None):
    def run(args,**kwargs):
        if args[0]=='git':return factory.execute(args,**kwargs)
        if Path(args[1]).name=='build.sh':
            env=kwargs['env']
            assert env['REDUX_TFT_PROFILE']=='none' and env['REDUX_DEV_LOGIN']=='0'
            assert 'REDUX_OPERATOR_PUBKEY_FILE' not in env
            deploy=Path(env['REDUX_BUILD_DIR'])/'pi-gen/deploy';deploy.mkdir(parents=True)
            image=artifacts[profile][0]
            for suffix in ('','.release.json','.sha256'):shutil.copyfile(str(image)+suffix,str(deploy/image.name)+suffix)
            if change:change(deploy/image.name)
        elif Path(args[1]).name=='make-bundle.sh':
            # Build the same standard bundle from the synthetic root, without
            # invoking the root-only loop/mount helper in a software test.
            import tarfile
            from bundle_audit import HOOK
            bundle=Path(args[3]);incoming=bundle.parent/'test-bundle';incoming.mkdir()
            a,boot=artifacts[profile][1:]
            def include(member):
                name=member.name.removeprefix('./')
                return None if any(name.startswith(p+'/') for p in ('captures','boot/firmware','boot/redux-control')) else member
            with tarfile.open(incoming/'rootfs.tar','w') as stream:stream.add(a,arcname='.',filter=include)
            with tarfile.open(incoming/'boot.tar','w') as stream:stream.add(boot,arcname='.')
            (incoming/'hook.sh').write_bytes(HOOK);(incoming/'hook.sh').chmod(0o755)
            revision=(a/'usr/share/redux/redux-revision').read_text().strip()
            (incoming/'manifest.raucm').write_text(fixtures.valid_manifest().replace('version=test','version=factory-test').replace('build='+fixtures.REV,'build='+revision))
            factory.execute(['rauc','--cert',str(certificates[0][0]),'--key',str(certificates[0][1]),'bundle','--mksquashfs-args=-processors 1',str(incoming),str(bundle)],**kwargs)
        else:raise AssertionError(args)
    return run


@requires_tools
@pytest.mark.parametrize('profile',['lite','ab'])
def test_actual_factory_artifact_publication_and_reverification(factory_artifacts,certificates,tmp_path,monkeypatch,profile):
    _,root,revision,artifacts=factory_artifacts
    monkeypatch.setattr(factory,'host_report',lambda p:{'build_ready':True,'blockers':[],'unverified':[]})
    args=dict(revision=revision,profile=profile,country='US',workspace=tmp_path/'attempt',output=tmp_path/'candidate',captures_mib=448)
    if profile=='ab':args.update(cert=certificates[0][0],key=certificates[0][1],keyring=certificates[0][0],version='factory-test')
    result=factory.build(root,**args,runner=fake_builder(artifacts,profile,certificates))
    assert result['factory_completed'] is True,result
    assert result['result']=='UNKNOWN' and result['physical_boot_verified'] is False
    assert result['validation']['evidence']['runtime']['package_inventories'][0]['count']==6  # six required runtime packages in slot fixture
    assert not list(args['output'].glob('*.key'))
    inventory=factory.inspect(args['output'])
    assert len(inventory['files'])==(5 if profile=='ab' else 3)
    checked=factory.verify(args['output'],keyring=certificates[0][0] if profile=='ab' else None)
    assert checked['result']=='UNKNOWN'
    assert any(row['name']=='Independent artifact authenticity' and row['status']=='UNKNOWN' for row in checked['checks'])
    factory.completed_validation(checked,profile)
    assert factory.preflight(root,**args)['result']=='FAIL'
    tampered=tmp_path/'tampered'
    shutil.copytree(args['output'],tampered)
    artifact=tampered/result['image']
    with artifact.open('ab') as stream:stream.write(b'tamper')
    with pytest.raises(ValueError):factory.inspect(tampered)


@requires_tools
@pytest.mark.parametrize('case',['truncated','profile','manifest','source_changed','revision','upstream','commit_changed'])
def test_incomplete_builds_never_publish_completed_receipt(factory_artifacts,certificates,tmp_path,monkeypatch,case):
    _,original,revision,artifacts=factory_artifacts
    root=tmp_path/'source'
    command(['git','clone','-q',str(original),str(root)])
    monkeypatch.setattr(factory,'host_report',lambda p:{'build_ready':True,'blockers':[],'unverified':[]})
    args=dict(revision=revision,profile='lite',country='US',workspace=tmp_path/'attempt',output=tmp_path/'candidate',captures_mib=448)
    def corrupt(image):
        if case=='truncated':image.write_bytes(image.read_bytes()[:100])
        elif case=='profile':
            manifest=Path(str(image)+'.release.json');value=json.loads(manifest.read_text());value['image_profile']='ab';manifest.write_text(json.dumps(value))
        elif case=='manifest':Path(str(image)+'.release.json').write_text('{invalid')
        elif case=='source_changed':(root/'build.sh').write_text('concurrent mutation')
        elif case in ('revision','upstream'):
            manifest=Path(str(image)+'.release.json');value=json.loads(manifest.read_text());value['revisions']['redux' if case=='revision' else 'pi_gen']='d'*40;manifest.write_text(json.dumps(value))
        elif case=='commit_changed':
            (root/'build.sh').write_text('new committed revision');command(['git','-C',str(root),'add','.']);command(['git','-C',str(root),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','concurrent source revision'])
    result=factory.build(root,**args,runner=fake_builder(artifacts,'lite',certificates,change=corrupt))
    assert result['result']=='FAIL' and result['factory_completed'] is False
    assert not (args['output']/'factory.json').exists()
    assert json.loads((args['workspace']/'attempt.json').read_text())['state']=='failed'


@pytest.mark.parametrize('target',['/boot/redux-no-write','/etc/redux-no-write','/dev/redux-no-write','/captures/redux-no-write'])
def test_system_and_capture_output_trees_are_rejected(inputs,target):
    root,args=inputs;args['output']=Path(target)
    assert factory.preflight(root,**args)['result']=='FAIL'


def test_final_receipt_is_never_overwritten(tmp_path):
    original={'state':'old'}
    factory.write_record(tmp_path,'factory.json',original)
    with pytest.raises(FileExistsError):factory.write_record(tmp_path,'factory.json',{'state':'new'})
    assert json.loads((tmp_path/'factory.json').read_text())==original


@requires_tools
@pytest.mark.parametrize('case',['bad_signature','incomplete_bundle','capacity','output_race'])
def test_signed_or_publication_failures_preserve_prior_evidence(factory_artifacts,certificates,tmp_path,monkeypatch,case):
    _,root,revision,artifacts=factory_artifacts
    monkeypatch.setattr(factory,'host_report',lambda p:{'build_ready':True,'blockers':[],'unverified':[]})
    profile='ab' if case in ('bad_signature','incomplete_bundle') else 'lite'
    args=dict(revision=revision,profile=profile,country='US',workspace=tmp_path/'attempt',output=tmp_path/'candidate',captures_mib=448)
    if profile=='ab':args.update(cert=certificates[0][0],key=certificates[0][1],keyring=certificates[0][0],version='factory-test')
    if case=='capacity':args['captures_mib']=512
    base=fake_builder(artifacts,profile,certificates)
    def runner(command,**kwargs):
        base(command,**kwargs)
        if command[0]=='bash' and Path(command[1]).name=='make-bundle.sh':
            bundle=Path(command[3]);data=bundle.read_bytes()
            bundle.write_bytes(data[:128] if case=='incomplete_bundle' else data[:-16]+b'X'*16)
        if case=='output_race' and command[0]=='bash' and Path(command[1]).name=='build.sh':
            args['output'].mkdir();(args['output']/'old').write_text('preserve concurrent artifact')
    result=factory.build(root,**args,runner=runner)
    assert result['result']=='FAIL' and result['factory_completed'] is False
    assert not (args['output']/'factory.json').exists()
    if case=='output_race':assert (args['output']/'old').read_text()=='preserve concurrent artifact'


def test_entrypoint_preflight_uses_factory_without_sudo_or_writes(inputs,monkeypatch,capsys):
    root,args=inputs
    spec=importlib.util.spec_from_file_location('factory_entrypoint',ROOT/'scripts/redux-release.py')
    entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    monkeypatch.setattr(entry,'ROOT',root)
    code=entry.main(['--json','factory','preflight','--revision',args['revision'],'--profile','lite','--country','US',
                     '--workspace',str(args['workspace']),'--out',str(args['output'])])
    assert code==0
    result=json.loads(capsys.readouterr().out)
    assert result['operation']=='preflight' and result['image_built'] is False
    assert not args['workspace'].exists()
