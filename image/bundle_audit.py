"""Authenticate a RAUC verity bundle and inspect standard archives without installation."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tempfile

from boot import ota
from offline_payload import MAX_DISK, MAX_FILE, ToolUnavailable, run, contract_paths, _link_allowed
from update_safety import public_keyring, read, json_record, safe_path, snapshot_file

HOOK = b'#!/bin/sh\nexec /usr/local/bin/redux-ota hook "$@"\n'
HASH = re.compile(r'[0-9a-f]{64}')


def _member_name(name):
    if name.startswith('./'):
        name = name[2:]
    if name in ('','.'):
        return ''
    if name.startswith('/') or '\\' in name or any(p in ('','..','.') for p in name.split('/')):
        raise ValueError('archive path traversal or ambiguous member')
    return name


def archive_snapshot(archive, destination, *, kind, budget=MAX_DISK):
    """Validate every member; copy ONLY selected regular files and safe links.

    No tar extract/extractall, device nodes, hardlink creation or guest code.
    Root guest symlinks outside the selected paths are never materialized.
    """
    wanted = set(contract_paths('ab')[0]) | set(contract_paths('ab')[1]) if kind=='rootfs' else {
        'kernel8.img','kernel_2712.img','start4.elf','fixup4.dat',
        'bcm2711-rpi-4-b.dtb','bcm2712-rpi-5-b.dtb','config.txt','cmdline.txt'}
    destination = Path(destination)
    destination.mkdir()
    records, total = {}, 0
    with tarfile.open(archive,mode='r:') as stream:
        for member in stream:
            name = _member_name(member.name)
            if not name:
                if not member.isdir():
                    raise ValueError('archive root is not a directory')
                continue
            if name in records or len(records) >= 200000:
                raise ValueError('duplicate or excessive archive members')
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError('special files are forbidden in update archives')
            if kind=='rootfs' and any(name.startswith(prefix+'/') for prefix in ('captures','boot/firmware','boot/redux-control')):
                raise ValueError('update archive includes persistent captures or boot selector content')
            if kind=='boot' and not (member.isfile() or member.isdir()):
                raise ValueError('boot archive cannot contain symlinks or hardlinks')
            if member.size < 0 or member.size > budget:
                raise ValueError('invalid archive member length')
            total += member.size
            if total > budget:
                raise ValueError('archive exceeds offline inspection budget')
            if member.islnk():
                _member_name(member.linkname)
            records[name] = member
        for name, member in records.items():
            for parent in PurePosixPath(name).parents:
                ancestor = records.get(str(parent))
                if ancestor is not None and not ancestor.isdir():
                    raise ValueError('archive member beneath link/non-directory')
            if member.islnk():
                target = records.get(_member_name(member.linkname))
                if target is None or not target.isfile():
                    raise ValueError('dangling or chained archive hardlink')
            if name not in wanted:
                continue
            path = safe_path(destination,name)
            path.parent.mkdir(parents=True,exist_ok=True)
            if member.isfile():
                if member.size > MAX_FILE:
                    raise ValueError('selected archive file too large')
                data = stream.extractfile(member)
                with path.open('xb') as output:
                    remaining = member.size
                    while remaining:
                        chunk = data.read(min(1024*1024,remaining))
                        if not chunk:
                            raise ValueError('truncated archive member')
                        output.write(chunk)
                        remaining -= len(chunk)
                path.chmod(member.mode & 0o777)
            elif member.issym() and kind=='rootfs' and _link_allowed(name,member.linkname):
                path.symlink_to(member.linkname)
            else:
                raise ValueError('unsupported selected archive inode')
    if kind=='rootfs':
        (destination/'captures').mkdir(exist_ok=True)
    return destination


def verify_bundle(bundle, keyring, scratch, *, revision, version=None):
    bundle,keyring,scratch = Path(bundle),Path(keyring),Path(scratch)
    scratch=Path(tempfile.mkdtemp(prefix='redux-bundle-',dir=scratch))
    # safe read/regular checks are deliberately independent of tool success.
    trusted_bytes=read(keyring.parent,keyring.name)
    public_keyring(trusted_bytes)
    keyring=scratch/'rauc-trust.pem'
    keyring.write_bytes(trusted_bytes)
    original=bundle
    meta = bundle.lstat()
    safe_path(bundle.parent,bundle.name)
    if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or not 0 < meta.st_size <= MAX_DISK:
        raise ValueError('bundle must be a nonempty singly-linked regular file')
    bundle=snapshot_file(bundle,scratch/'inspection.raucb')
    version_text=run(['rauc','--version']).decode().strip()
    match=re.fullmatch(r'rauc (\d+)\.(\d+)(?:\.\d+)?',version_text)
    if match is None or tuple(map(int,match.groups())) < (1,10):
        raise ToolUnavailable('host RAUC >=1.10 with authenticated json-2 metadata is required')
    config=scratch/'host-inspection.conf'
    config.write_text('[system]\ncompatible='+ota.COMPATIBLE+'\nbootloader=noop\nbundle-formats=verity\n')
    info = json_record(run(['rauc','--conf',str(config),'--keyring',str(keyring),'info','--output-format=json-2',str(bundle)],timeout=120))
    if (not isinstance(info.get('update'),dict) or not isinstance(info.get('bundle'),dict)
            or info['update'].get('compatible') != ota.COMPATIBLE
            or info['update'].get('build') != revision
            or info['bundle'].get('format') != 'verity'):
        raise ValueError('signed bundle build/profile/format is incompatible')
    if version is not None and info['update'].get('version') != version:
        raise ValueError('signed bundle release version differs from requested version')
    images = info.get('images')
    if not isinstance(images,list) or len(images)!=2 or {item.get('slot-class') for item in images if isinstance(item,dict)} != {'boot','rootfs'}:
        raise ValueError('signed bundle must have exactly boot/rootfs images')
    for item in images:
        if (item.get('filename') != item['slot-class']+'.tar'
                or type(item.get('size')) is not int or not 0 < item['size'] <= MAX_DISK
                or not isinstance(item.get('checksum'),str) or not HASH.fullmatch(item['checksum'])):
            raise ValueError('invalid signed image name/size/digest')
    if sum(item['size'] for item in images) + 128*1024 > __import__('shutil').disk_usage(scratch).free:
        raise ValueError('insufficient temporary space for authenticated bundle payloads')
    inventory = run(['unsquashfs','-lln',str(bundle)],timeout=120).decode()
    entries = {}
    for line in inventory.splitlines():
        fields = line.split()
        if len(fields)!=6 or not re.fullmatch(r'[dr-][rwxstST-]{9}',fields[0]):
            raise ValueError('unsupported SquashFS directory inventory')
        name=fields[-1]
        if name in entries:
            raise ValueError('duplicate bundle entry')
        entries[name] = fields[0][0]
    expected = {'squashfs-root':'d',**{'squashfs-root/'+name:'-' for name in ('manifest.raucm','hook.sh','rootfs.tar','boot.tar')}}
    if entries != expected:
        raise ValueError('bundle contains links, special files, unexpected directories or incomplete payload')
    # RAUC extract authenticates the signature AND verifies the complete verity
    # payload before unsquashing. Inventory above confines extraction to four
    # ordinary filenames; no hook/handler is ever executed.
    extracted = scratch/'verified-bundle'
    run(['rauc','--conf',str(config),'--keyring',str(keyring),'extract',str(bundle),str(extracted)],timeout=300)
    for name in ('manifest.raucm','hook.sh','rootfs.tar','boot.tar'):
        safe_path(extracted,name)
    manifest = ota.validate_manifest(read(extracted,'manifest.raucm').decode())
    if manifest.get('update','build',fallback='') != revision or manifest.get('update','version',fallback='') != info['update'].get('version'):
        raise ValueError('extracted manifest contradicts authenticated metadata')
    if read(extracted,'hook.sh',8192) != HOOK:
        raise ValueError('bundle post-install hook is not the approved wrapper')
    for item in images:
        path = safe_path(extracted,item['filename'])
        st = path.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size != item['size']:
            raise ValueError('incomplete or linked bundle image')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                digest.update(block)
        if digest.hexdigest()!=item['checksum']:
            raise ValueError('bundle archive digest differs from signed metadata')
        section='image.'+item['slot-class']
        if manifest.get(section,'sha256') != item['checksum'] or manifest.getint(section,'size') != item['size']:
            raise ValueError('bundle manifest contradicts image digest/size')
    root = archive_snapshot(extracted/'rootfs.tar',scratch/'bundle-root',kind='rootfs')
    boot = archive_snapshot(extracted/'boot.tar',scratch/'bundle-boot',kind='boot')
    if read(root,'usr/share/redux/redux-revision',256).decode().strip() != revision:
        raise ValueError('bundle rootfs baked revision contradicts signed build identity')
    after=original.lstat()
    if (meta.st_dev,meta.st_ino,meta.st_size,meta.st_mtime_ns,meta.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
        raise ValueError('bundle changed during verification')
    return {'signature_verified':True,'verity_payload_verified':True,
            'compatible':ota.COMPATIBLE,'revision':revision,'version':info['update'].get('version'),
            'root':root,'boot':boot}
