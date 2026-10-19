"""Real loop-free filesystem and signed-bundle integration; all keys/data synthetic."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import lzma
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import uuid
import zlib

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'image'))
import bundle_audit
import offline_payload
import qualify_candidate as gate
from release_manifest import publish

spec=importlib.util.spec_from_file_location('candidate_fixtures',ROOT/'tests/test_update_framework.py')
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
certificates=fixtures.certificates
REV=fixtures.REV

TOOLS=('debugfs','mkfs.ext4','mkfs.vfat','mcopy','mtype','rauc','mksquashfs','unsquashfs')
HAS_TOOLS=all(shutil.which(name) for name in TOOLS)
requires_tools=pytest.mark.skipif(not HAS_TOOLS,reason='optional real-artifact tests require host RAUC/FAT/ext4/SquashFS tools; pure policy tests still run')


def command(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=120,check=False)
    assert result.returncode==0,result.stderr+result.stdout


def fat(folder,name,size,label,source):
    target=folder/name
    with target.open('wb') as stream:stream.truncate(size)
    command(['mkfs.vfat','-F','16' if label=='REDUXCTRL' else '32','-n',label,str(target)])
    for path in source.iterdir():
        command(['mcopy','-i',str(target),str(path),'::'+path.name])
    return target


def ext(folder,name,size,label,source=None):
    target=folder/name
    with target.open('wb') as stream:stream.truncate(size)
    args=['mkfs.ext4','-q','-F','-L',label]
    if source is not None:args+=['-d',str(source)]
    command(args+[str(target)])
    return target


def disk(folder,parts,profile):
    target=folder/'synthetic.img'
    starts=[];position=2048
    for p in parts:
        starts.append(position);position+=p.stat().st_size//512
    sectors=position+34 if profile=='ab' else position
    with target.open('wb') as stream:stream.truncate(sectors*512)
    mbr=bytearray(512);mbr[510:512]=b'\x55\xaa'
    if profile=='ab':
        mbr[450]=0xee;struct.pack_into('<II',mbr,454,1,sectors-1)
        entries=bytearray(128*128)
        names=('REDUXCTRL','REDUXBOOTA','REDUXBOOTB','REDUXROOTA','REDUXROOTB','REDUXCAP')
        for i,(name,start,p) in enumerate(zip(names,starts,parts)):
            offset=i*128
            kind='ebd0a0a2-b9e5-4433-87c0-68b6b72699c7' if i<3 else '0fc63daf-8483-4772-8e79-3d69d8477de4'
            entries[offset:offset+16]=uuid.UUID(kind).bytes_le
            entries[offset+16:offset+32]=uuid.UUID(fixtures.UUIDS[str(i+1)]).bytes_le
            struct.pack_into('<QQ',entries,offset+32,start,start+p.stat().st_size//512-1)
            encoded=name.encode('utf-16-le');entries[offset+56:offset+56+len(encoded)]=encoded
        def header(this,other,entry_lba):
            out=bytearray(512);out[:8]=b'EFI PART'
            struct.pack_into('<IIII',out,8,0x10000,92,0,0)
            struct.pack_into('<QQQQ',out,24,this,other,34,sectors-34)
            out[56:72]=uuid.UUID('ffffffff-1234-1234-1234-123456789abc').bytes_le
            struct.pack_into('<QIII',out,72,entry_lba,128,128,zlib.crc32(entries))
            struct.pack_into('<I',out,16,zlib.crc32(out[:92]))
            return out
        with target.open('r+b') as out:
            out.write(mbr);out.write(header(1,sectors-1,2));out.write(entries)
            out.seek((sectors-33)*512);out.write(entries);out.write(header(sectors-1,1,sectors-33))
    else:
        struct.pack_into('<I',mbr,440,0x12345678)
        for i,(start,p) in enumerate(zip(starts,parts)):
            offset=446+i*16;mbr[offset+4]=0x0c if i==0 else 0x83
            struct.pack_into('<II',mbr,offset+8,start,p.stat().st_size//512)
        with target.open('r+b') as out:out.write(mbr)
    with target.open('r+b') as out:
        for start,p in zip(starts,parts):
            out.seek(start*512)
            with p.open('rb') as source:shutil.copyfileobj(source,out,1024*1024)
    compressed=folder/'synthetic.img.xz'
    with target.open('rb') as source,lzma.open(compressed,'wb',preset=0) as out:
        shutil.copyfileobj(source,out,1024*1024)
    record=publish(compressed,REV,'b'*40,'c'*40,minimum_bytes=512,expected_profile=profile)
    return compressed,record


def add_boot(root,slot=None):
    boot=root/'boot/firmware';boot.mkdir(parents=True,exist_ok=True)
    import boot_payload
    for name in boot_payload._KERNEL_AND_FIRMWARE:
        fixtures.put(boot,name,hashlib.shake_256(name.encode()).digest(16384))
    fixtures.put(boot,'config.txt','[all]\narm_64bit=1\nkernel_watchdog_timeout=60\n')
    root_id=fixtures.UUIDS['4' if slot=='A' else '5'] if slot else '12345678-02'
    fixtures.put(boot,'cmdline.txt','root=PARTUUID='+root_id+' rootwait'+(' rauc.slot='+slot+' panic=10' if slot else '')+'\n')
    return boot


@pytest.fixture(scope='module')
def real_release(tmp_path_factory,certificates):
    if not HAS_TOOLS:pytest.skip('optional host artifact tools absent')
    folder=tmp_path_factory.mktemp('real-offline-release')
    a=fixtures.configured_root(folder,certificates,'A');b=fixtures.configured_root(folder,certificates,'B')
    a_boot=add_boot(a,'A');b_boot=add_boot(b,'B')
    ctrl=a/'boot/redux-control'
    fixtures.put(ctrl,'autoboot.txt',fixtures.ota.autoboot('A'))
    fixtures.put(ctrl,'config.txt','[all]\narm_64bit=1\n')
    fixtures.put(ctrl,'start.elf',b'firmware-selector-test')
    mib=1024*1024
    parts=[fat(folder,'ctrl',32*mib,'REDUXCTRL',ctrl),fat(folder,'boot-a',64*mib,'REDUXBOOTA',a_boot),
           fat(folder,'boot-b',64*mib,'REDUXBOOTB',b_boot),ext(folder,'root-a',16*mib,'REDUXROOTA',a),
           ext(folder,'root-b',16*mib,'REDUXROOTB',b),ext(folder,'captures',8*mib,'REDUXCAP')]
    image,record=disk(folder,parts,'ab')
    incoming=folder/'bundle-input';incoming.mkdir()
    with tarfile.open(incoming/'rootfs.tar','w') as out:
        def include(member):
            name=member.name.removeprefix('./')
            return None if any(name.startswith(prefix+'/') for prefix in ('captures','boot/firmware','boot/redux-control')) else member
        out.add(a,arcname='.',filter=include)
    with tarfile.open(incoming/'boot.tar','w') as out:out.add(a_boot,arcname='.')
    fixtures.put(incoming,'hook.sh',bundle_audit.HOOK).chmod(0o755)
    manifest=fixtures.valid_manifest().replace('version=test','version=integration')
    fixtures.put(incoming,'manifest.raucm',manifest)
    bundle=folder/'synthetic.raucb'
    command(['rauc','--cert',str(certificates[0][0]),'--key',str(certificates[0][1]),'bundle',
             '--mksquashfs-args=-processors 1',str(incoming),str(bundle)])
    return folder,image,record,bundle,a


@requires_tools
def test_actual_image_and_cryptographically_verified_bundle(real_release,certificates):
    folder,image,record,bundle,_=real_release
    result=gate.qualify(image,revision=REV,profile='ab',country='US',trusted_sha256=record['sha256'],
                        bundle=bundle,keyring=certificates[0][0],version='integration',minimum_bytes=512)
    assert result['offline_qualified'],result
    assert result['result']=='UNKNOWN' and not result['install_permitted']
    assert result['evidence']['bundle']['signature_verified']
    assert result['evidence']['bundle']['verity_payload_verified']
    assert not result['automatic_rollback_verified']


@requires_tools
def test_wrong_signer_and_incompatible_build_rejected(real_release,certificates,tmp_path):
    _,_,_,bundle,_=real_release
    with pytest.raises(ValueError):bundle_audit.verify_bundle(bundle,certificates[1][0],tmp_path,revision=REV)
    with pytest.raises(ValueError,match='incompatible'):
        bundle_audit.verify_bundle(bundle,certificates[0][0],tmp_path,revision='f'*40)


@requires_tools
def test_interrupted_or_modified_bundle_rejected(real_release,certificates,tmp_path):
    _,_,_,bundle,_=real_release
    broken=tmp_path/'interrupted.raucb';broken.write_bytes(bundle.read_bytes()[:-20])
    with pytest.raises(ValueError):bundle_audit.verify_bundle(broken,certificates[0][0],tmp_path,revision=REV)
    tampered=tmp_path/'modified.raucb';data=bytearray(bundle.read_bytes());data[4096]^=1;tampered.write_bytes(data)
    with pytest.raises(ValueError):bundle_audit.verify_bundle(tampered,certificates[0][0],tmp_path,revision=REV)


@requires_tools
def test_actual_lite_image_runtime_without_mounts(tmp_path):
    root=fixtures.fixture_module('root_payload').slot(tmp_path)
    fixtures.put(root,'usr/share/redux/redux-revision',REV)
    fixtures.put(root,'etc/redux/regulatory-country','US\n')
    boot=add_boot(root)
    mib=1024*1024
    parts=[fat(tmp_path,'boot',64*mib,'BOOT',boot),ext(tmp_path,'root',16*mib,'ROOTFS',root),ext(tmp_path,'captures',8*mib,'REDUXCAP')]
    image,record=disk(tmp_path,parts,'lite')
    result=gate.qualify(image,revision=REV,profile='lite',country='US',trusted_sha256=record['sha256'],minimum_bytes=512)
    assert result['offline_qualified'],result
    assert result['evidence']['image']['sha256']==record['sha256']


@pytest.mark.parametrize('name',['../escape','/absolute','a/../../outside','a//b'])
def test_archive_traversal_never_extracts(tmp_path,name):
    archive=tmp_path/'bad.tar'
    with tarfile.open(archive,'w') as out:
        item=tarfile.TarInfo(name);out.addfile(item)
    with pytest.raises(ValueError):bundle_audit.archive_snapshot(archive,tmp_path/'result',kind='rootfs')
    assert not (tmp_path.parent/'escape').exists()


@pytest.mark.parametrize('case',['symlink-parent','hardlink-escape','captures','control','device','duplicate'])
def test_archive_link_data_partition_and_special_file_defenses(tmp_path,case):
    archive=tmp_path/'bad.tar'
    with tarfile.open(archive,'w') as out:
        first=tarfile.TarInfo('etc')
        if case=='symlink-parent':first.type=tarfile.SYMTYPE;first.linkname='/tmp'
        elif case=='hardlink-escape':first.type=tarfile.LNKTYPE;first.linkname='../outside'
        elif case=='captures':first.name='captures/ready/private'
        elif case=='control':first.name='boot/redux-control/autoboot.txt'
        elif case=='device':first.type=tarfile.CHRTYPE
        out.addfile(first)
        if case=='symlink-parent':out.addfile(tarfile.TarInfo('etc/child'))
        if case=='duplicate':out.addfile(first)
    with pytest.raises(ValueError):bundle_audit.archive_snapshot(archive,tmp_path/'result',kind='rootfs')


def test_incomplete_manifest_and_missing_trust_fail_without_devices(tmp_path):
    fake=tmp_path/'missing.img.xz'
    result=gate.qualify(fake,revision=REV,profile='ab',country='US')
    assert result['result']=='FAIL' and not result['offline_qualified']
    assert not result['install_permitted']


@requires_tools
def test_release_manifest_contradictions_and_digests(real_release):
    _,image,record,_,_=real_release
    for kwargs in ({'revision':'f'*40},{'trusted_sha256':'0'*64},{'profile':'lite'}):
        args=dict(revision=REV,profile='ab',country='US',trusted_sha256=record['sha256'],minimum_bytes=512)
        args.update(kwargs)
        result=gate.qualify(image,**args)
        assert result['result']=='FAIL' and not result['offline_qualified']


def test_missing_host_tools_is_unknown_not_a_mocked_pass(tmp_path,monkeypatch):
    f=fixtures.fixture_module('release_gpt')
    image=f.sample(tmp_path)
    record=publish(image,REV,'b'*40,'c'*40,minimum_bytes=512,expected_profile='ab')
    monkeypatch.setattr(offline_payload.shutil,'which',lambda name:None)
    result=gate.qualify(image,revision=REV,profile='ab',country='US',trusted_sha256=record['sha256'],minimum_bytes=512)
    assert result['result']=='UNKNOWN' and not result['offline_qualified']


@requires_tools
def test_symlinked_ancestor_and_bad_runtime_cannot_be_certified(tmp_path,real_release):
    folder,image,record,_,_=real_release
    linked=tmp_path/'linked';linked.symlink_to(folder,target_is_directory=True)
    assert gate.qualify(linked/image.name,revision=REV,profile='ab',country='US',minimum_bytes=512)['result']=='FAIL'
    f=fixtures.fixture_module('release_gpt');bad=f.sample(tmp_path)
    r=publish(bad,REV,'b'*40,'c'*40,minimum_bytes=512,expected_profile='ab')
    result=gate.qualify(bad,revision=REV,profile='ab',country='US',trusted_sha256=r['sha256'],minimum_bytes=512)
    assert result['result']=='FAIL' and not result['offline_qualified']


def test_decompression_budget_is_enforced(tmp_path):
    f=fixtures.fixture_module('release_manifest')
    image=f.fixture_image(tmp_path)
    with pytest.raises(ValueError,match='budget'):
        from release_manifest import inspect_image
        inspect_image(image,minimum_bytes=512,maximum_bytes=1024)


def test_malformed_tar_is_not_an_unhandled_candidate_exception(tmp_path):
    archive=tmp_path/'incomplete.tar';archive.write_bytes(b'not-a-tar')
    with pytest.raises(tarfile.TarError):
        bundle_audit.archive_snapshot(archive,tmp_path/'out',kind='rootfs')


@requires_tools
def test_same_revision_with_contradictory_factory_runtime_is_rejected(real_release,tmp_path):
    folder,_,_,_,_=real_release
    part=tmp_path/'different-root-b'
    shutil.copyfile(folder/'root-b',part)
    replacement=fixtures.put(tmp_path,'replacement','contradictory factory runtime\n')
    command(['debugfs','-w','-R','rm /opt/redux/redux/core/live_runtime.py',str(part)])
    command(['debugfs','-w','-R',f'write {replacement} /opt/redux/redux/core/live_runtime.py',str(part)])
    raw=tmp_path/'changed.img'
    shutil.copyfile(folder/'synthetic.img',raw)
    # The actual GPT's fifth partition location, not a guessed physical device.
    with raw.open('rb') as stream:
        stream.seek(1024+4*128+32);offset=int.from_bytes(stream.read(8),'little')*512
    with raw.open('r+b') as out,part.open('rb') as source:
        out.seek(offset);shutil.copyfileobj(source,out)
    image=tmp_path/'changed.img.xz'
    with raw.open('rb') as source,lzma.open(image,'wb',preset=0) as out:shutil.copyfileobj(source,out,1024*1024)
    record=publish(image,REV,'b'*40,'c'*40,minimum_bytes=512,expected_profile='ab')
    result=gate.qualify(image,revision=REV,profile='ab',country='US',trusted_sha256=record['sha256'],minimum_bytes=512)
    assert result['result']=='FAIL' and not result['offline_qualified']
    assert any('does not match' in row['detail'] for row in result['checks'])


@pytest.mark.parametrize('channel',['stdout','stderr'])
def test_host_parser_output_is_bounded_and_reaped(channel):
    code='import sys; sys.'+channel+'.write("x"*100000)'
    with pytest.raises(ValueError,match='exceeded limit'):
        offline_payload.run([sys.executable,'-c',code],limit=10,timeout=5)


def test_host_parser_timeout_is_bounded_and_reaped():
    with pytest.raises(ValueError,match='timed out'):
        offline_payload.run([sys.executable,'-c','import time;time.sleep(10)'],timeout=.05)
