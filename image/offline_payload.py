"""Allowlisted extraction from a compressed image; no mounts, devices or guest execution.

Tools parse regular temporary partition copies. Only known contract paths are
materialized, with validated inode types and confined systemd/config symlinks.
Captures and unrelated customer files are never extracted.
"""
from __future__ import annotations
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import selectors
import signal
import time
import subprocess
import uuid

import boot_payload
import root_payload
from update_safety import safe_path

MAX_FILE = 64 * 1024 * 1024
MAX_DISK = 64 * 1024 * 1024 * 1024


class ToolUnavailable(RuntimeError):
    pass


def run(args, *, limit=65536, timeout=30, output=None, cwd=None):
    if shutil.which(args[0]) is None:
        raise ToolUnavailable('host tool required: ' + args[0])
    # Drain both pipes with a hard byte/deadline budget. A malformed parser
    # cannot fill RAM or the host's temporary filesystem before a final check.
    import tempfile
    with tempfile.TemporaryFile() as log:
        child=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,cwd=cwd,start_new_session=True,
                               env=dict(os.environ,MTOOLSRC="/dev/null"))
        deadline=time.monotonic()+timeout
        stderr_size=0
        def stop():
            try:
                os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout,selectors.EVENT_READ,'stdout')
                selector.register(child.stderr,selectors.EVENT_READ,'stderr')
                while selector.get_map():
                    remaining=deadline-time.monotonic()
                    if remaining<=0:
                        raise ValueError('offline inspection tool timed out: '+args[0])
                    for key,_ in selector.select(remaining):
                        chunk=os.read(key.fileobj.fileno(),65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if key.data=='stdout':
                            if log.tell()+len(chunk)>limit:
                                raise ValueError('offline inspection stdout exceeded limit: '+args[0])
                            log.write(chunk)
                        else:
                            stderr_size+=len(chunk)
                            if stderr_size>65536:
                                raise ValueError('offline inspection stderr exceeded limit: '+args[0])
                result=child.wait(timeout=max(0.001,deadline-time.monotonic()))
            if result:
                detail='; check trusted certificates, complete bundle and verity payload' if args[0]=='rauc' else '; check filesystem integrity and tool support'
                raise ValueError('offline inspection failed: '+args[0]+detail)
        except subprocess.TimeoutExpired as exc:
            stop()
            raise ValueError('offline inspection tool timed out: '+args[0]) from exc
        except BaseException:
            stop()
            raise
        finally:
            child.stdout.close();child.stderr.close()
        size=log.tell()
        log.seek(0)
        if output is not None:
            with Path(output).open('xb') as stream:
                shutil.copyfileobj(log,stream,1024*1024)
            return size
        return log.read()


def contract_paths(profile):
    required = set(root_payload._MUST_EXIST) | {
        'etc/redux/live.toml','etc/redux/pipeline.toml','etc/redux/regulatory-country',
        'etc/overlayroot.conf','etc/fstab', 'etc/systemd/system/bettercap.service',
        'etc/systemd/system/resize2fs_once.service'}
    optional = {'etc/init.d/resize2fs_once','captures/config/fbui.env'}
    units = dict(root_payload._UNITS)
    if profile == 'ab':
        units.update(root_payload._AB_UNITS)
        required.update('etc/systemd/system/'+name for name in root_payload._AB_REQUIRED_UNITS)
        required.update({'etc/rauc/keyring.pem','etc/rauc/system.conf','opt/redux/boot/ota.py',
                         'etc/systemd/system/rauc.service.d/redux.conf',
                         'etc/systemd/system.conf.d/redux-watchdog.conf',
                         'usr/local/bin/redux-ota','usr/lib/systemd/system-generators/redux-ota'})
        required.update('usr/local/libexec/redux-rauc-'+name for name in
                        ('backend','preinstall','health','deadline','generator'))
    else:
        required.add('etc/systemd/system/rauc.service')
    for name,(group,*_) in units.items():
        required.update(('etc/systemd/system/'+name, 'etc/systemd/system/'+group+'/'+name))
    for group in ('multi-user.target.wants','graphical.target.wants','default.target.wants'):
        optional.add('etc/systemd/system/'+group+'/redux-fbui.service')
    return required, optional


def _stat(partition, guest):
    if not re.fullmatch(r'[A-Za-z0-9_./+-]+',guest) or '..' in PurePosixPath(guest).parts:
        raise ValueError('unsafe debugfs guest path')
    raw = run(['debugfs','-R','stat /'+guest,str(partition)]).decode('utf-8',errors='strict')
    match = re.search(r'Inode: \d+\s+Type: (regular|directory|symlink)\s+Mode:\s+([0-7]+)',raw)
    if not match:
        # debugfs can return 0 for missing paths; require affirmative inode data.
        if not raw.strip():
            return None
        raise ValueError('unsupported filesystem inode')
    size = re.search(r'\bSize:\s+(\d+)',raw)
    links = re.search(r'\bLinks:\s+(\d+)',raw)
    if size is None or links is None:
        raise ValueError('incomplete filesystem inode metadata')
    return match[1],int(match[2],8),int(size[1]),int(links[1]),raw


def _link_allowed(name,target):
    if name == 'usr/lib/systemd/system-generators/redux-ota':
        return target == '/usr/local/libexec/redux-rauc-generator'
    if name == 'etc/rauc/system.conf':
        return target == '/run/redux-ota/system.conf'
    if name.startswith('etc/systemd/system/'):
        leaf = name.rsplit('/',1)[-1]
        if target == '/dev/null':
            return leaf in ('rauc.service','bettercap.service','resize2fs_once.service')
        if '.wants/' in name:
            return target in ('../'+leaf,'/etc/systemd/system/'+leaf)
    return False


def extract_root(partition, destination, profile):
    required,optional = contract_paths(profile)
    destination = Path(destination)
    destination.mkdir()
    # Reject linked ancestors in the GUEST too. debugfs path lookup otherwise
    # follows symlinks before any host-side policy sees the selected file.
    parents = {}
    for name in sorted(required|optional):
        missing_parent = False
        for parent in PurePosixPath(name).parents:
            if str(parent) == '.':
                continue
            key = str(parent)
            if key not in parents:
                parents[key] = _stat(partition,key)
            info = parents[key]
            if info is None:
                missing_parent = True
                break
            if info[0] != 'directory':
                raise ValueError('linked/non-directory guest ancestor: '+key)
        info = None if missing_parent else _stat(partition,name)
        if info is None:
            if name in required:
                raise ValueError('missing embedded runtime file: '+name)
            continue
        kind,mode,size,links,raw = info
        if size > MAX_FILE or (kind == 'regular' and links != 1):
            raise ValueError('oversized or hard-linked embedded payload: '+name)
        path = safe_path(destination,name)
        path.parent.mkdir(parents=True,exist_ok=True)
        if kind == 'symlink':
            fast = re.search(r'^Fast link dest: "([^"\n]+)"$',raw,re.M)
            if fast:
                target = fast[1]
            else:
                run(['debugfs','-R',f'dump /{name} {path.name}',str(partition)],limit=1024,cwd=path.parent)
                if not path.is_file() or path.stat().st_size != size:
                    raise ValueError('cannot read guest symlink')
                target = path.read_text(encoding='ascii')
                path.unlink()
            if not _link_allowed(name,target):
                raise ValueError('unsupported guest symlink: '+name)
            path.symlink_to(target)
        elif kind == 'regular':
            # dump, unlike cat, supplies a host destination. Its arguments are
            # fixed allowlisted paths inside our fresh temporary directory.
            run(['debugfs','-R',f'dump /{name} {path.name}',str(partition)],limit=1024,cwd=path.parent)
            if not path.is_file() or path.stat().st_size != size:
                raise ValueError('incomplete embedded runtime extraction: '+name)
            path.chmod(mode & 0o777)
        else:
            raise ValueError('runtime file is not regular: '+name)
    (destination/'captures').mkdir(exist_ok=True)
    return destination


def extract_fat(partition,destination,names):
    destination = Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    for name in names:
        path = safe_path(destination,name)
        run(['mtype','-i',str(partition),'::'+name],limit=MAX_FILE,output=path)
        if path.stat().st_size == 0:
            raise ValueError('empty embedded FAT boot file: '+name)


def extract_image(image, measured, scratch, *, max_disk=MAX_DISK):
    """Copy only root/boot partitions; never mount, interpret or copy captures."""
    scratch = Path(scratch)
    if measured['disk_bytes'] > max_disk:
        raise ValueError('image exceeds offline inspection disk budget')
    selected = (range(1,6) if measured['image_profile']=='ab' else range(1,3))
    regions = {i:(p['start_lba']*512,(p['start_lba']+p['sector_count'])*512)
               for i,p in enumerate(measured['partitions'],1) if i in selected}
    if sum(end-start for start,end in regions.values()) > shutil.disk_usage(scratch).free:
        raise ValueError('insufficient temporary disk space for bounded partition copies')
    files = {i:scratch/f'partition-{i}' for i in regions}
    handles = {}
    try:
        handles = {i:path.open('xb') for i,path in files.items()}
        position = 0
        with lzma.open(image,'rb') as stream:
            while True:
                chunk = stream.read(1024*1024)
                if not chunk:
                    break
                for i,(start,end) in regions.items():
                    lo,hi=max(position,start),min(position+len(chunk),end)
                    if hi>lo:
                        handles[i].write(chunk[lo-position:hi-position])
                position += len(chunk)
                if position > max_disk:
                    raise ValueError('image expanded beyond inspection budget')
        if position != measured['disk_bytes']:
            raise ValueError('image length changed during payload inspection')
    finally:
        for handle in handles.values():
            handle.close()
    for i,(start,end) in regions.items():
        if files[i].stat().st_size != end-start:
            raise ValueError('incomplete partition copy')
    profile = measured['image_profile']
    roots = [extract_root(files[4 if profile=='ab' else 2],scratch/'root-a',profile)]
    if profile == 'ab':
        roots.append(extract_root(files[5],scratch/'root-b',profile))
    for root,i in zip(roots,(2,3) if profile=='ab' else (1,)):
        extract_fat(files[i],root/'boot/firmware',(*boot_payload._KERNEL_AND_FIRMWARE,'config.txt','cmdline.txt'))
    context = None
    if profile == 'ab':
        extract_fat(files[1],roots[0]/'boot/redux-control',('autoboot.txt','config.txt','start.elf'))
        # Unique partition identities were CRC-checked by inspect_image; read
        # their raw bytes from the same stream, not an operator-supplied map.
        with lzma.open(image,'rb') as stream:
            prefix = stream.read(34*512)
        fat_types={'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7','c12a7328-f81f-11d2-ba4b-00a0c93ec93b'}
        for i in range(6):
            kind=str(uuid.UUID(bytes_le=prefix[1024+i*128:1024+i*128+16]))
            if (i<3 and kind not in fat_types) or (i>=3 and kind!='0fc63daf-8483-4772-8e79-3d69d8477de4'):
                raise ValueError('unsupported GPT partition type GUID')
        devices = {str(i+1):'/dev/disk/by-partuuid/'+str(uuid.UUID(bytes_le=prefix[1024+i*128+16:1024+i*128+32])) for i in range(6)}
        context = {'current':'A','devices':devices}
    return roots,context
