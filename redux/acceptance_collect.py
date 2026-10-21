"""Explicit, unprivileged local acquisition. Never run on a device implicitly.

No SSH, sudo, writes, workload generation, radio/input operations or update calls.
The unsigned package is acquisition evidence, not hardware qualification. RF,
touch, persistence and destructive update tests require separate approved methods.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import time
import uuid
import urllib.request

from .boot_readiness import _read, _UNITS, service_probe, parse_mountinfo
from .hardware_acceptance import FORMAT, target, hash_value, number, validate
from .core.processing_evidence import scan_record_valid


def collector_digest():
    digest=hashlib.sha256()
    for name in ('acceptance_collect.py','hardware_acceptance.py','boot_readiness.py','core/telemetry.py','core/processing_evidence.py'):
        digest.update(name.encode()+b'\0'+(Path(__file__).parent/name).read_bytes()+b'\0')
    return digest.hexdigest()


def cached_status():
    """Consume the existing cached producer contract, no database/sensor scan.

    Fixed loopback only, no proxies, redirects, credentials or discovery.
    A protected or absent dashboard simply leaves measurements unavailable.
    """
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open('http://127.0.0.1:8080/api/status',timeout=2) as response:
        data=response.read(65537)
    if len(data)>65536:raise ValueError('cached status exceeds budget')
    value=json.loads(data)
    if not isinstance(value,dict):raise ValueError('invalid cached status')
    return value


def storage_observation(root=Path('/')):
    """Read kernel mounts and named block identity only; no opening block data."""
    raw=_read(root/'proc/self/mountinfo',limit=128*1024)
    if raw is None:raise ValueError('mount inventory unavailable')
    mounts=parse_mountinfo(raw)
    needed=('/', '/media/root-ro','/boot/firmware','/captures')
    if not all(name in mounts for name in needed):raise ValueError('mount prerequisites unavailable')
    def device(location):
        value=location.stat()
        if not stat.S_ISBLK(value.st_mode):raise ValueError('not a block identity')
        return f'{os.major(value.st_rdev)}:{os.minor(value.st_rdev)}'
    cap=device(root/'dev/disk/by-label/REDUXCAP')
    uuids={}
    directory=root/'dev/disk/by-partuuid'
    for i,entry in enumerate(directory.iterdir()):
        if i>=64:raise ValueError('partition identity budget exceeded')
        if not entry.is_symlink():continue
        if not re.fullmatch(r'[a-f0-9-]{8,36}',entry.name):raise ValueError('invalid partition UUID')
        identity=device(entry)
        if identity in uuids:raise ValueError('ambiguous partition UUID')
        uuids[identity]=entry.name
    cmd=_read(root/'proc/cmdline') or ''
    from boot import ota
    if 'rauc.slot=' in cmd:
        current=ota.slot_from_cmdline(cmd)
        labels=('REDUXCTRL','REDUXBOOTA','REDUXBOOTB','REDUXROOTA','REDUXROOTB','REDUXCAP')
        parts=[dict(index=i,label=label,major_minor=device(root/'dev/disk/by-partlabel'/label)) for i,label in enumerate(labels,1)]
        needed+=('/boot/redux-control',)
    else:
        current=None
        parts=[dict(index=i,label=label,major_minor=mounts[name]['device_major_minor']) for i,label,name in
               ((1,'','/boot/firmware'),(2,'','/media/root-ro'),(3,'REDUXCAP','/captures'))]
    for part in parts:
        part['uuid']=uuids[part['major_minor']]
        actual_index=_read(root/'sys/dev/block'/part['major_minor']/'partition',limit=8)
        if actual_index!=str(part['index']):raise ValueError('partition index mismatch')
        disk=(root/'sys/dev/block'/part['major_minor']).resolve(strict=True).parent
        part['disk_major_minor']=_read(disk/'dev',limit=32)
        if not isinstance(part['disk_major_minor'],str) or not re.fullmatch(r'\d{1,5}:\d{1,5}',part['disk_major_minor']):raise ValueError('partition parent unavailable')
    # Never include arbitrary mount sources, optional mount paths or options.
    lines=[]
    for i,name in enumerate(needed,1):
        m=mounts[name];options=','.join(k for k in m['options'] if k in ('ro','rw'))
        if not re.fullmatch(r'\d{1,5}:\d{1,5}',m['device_major_minor']):raise ValueError('invalid mount identity')
        if m['filesystem'] not in ('overlay','ext4','vfat'):raise ValueError('unsupported mount filesystem')
        lines.append(f"{i} 0 {m['device_major_minor']} / {name} {options} - {m['filesystem']} none {options}")
    directory=root/'captures/boot'
    if directory.is_symlink() or not directory.is_dir():raise ValueError('boot directory unverified')
    info=directory.stat();user=pwd.getpwnam('redux')
    return dict(mountinfo='\n'.join(lines)+'\n',label_major_minor=cap,current_slot=current,partitions=parts,
        boot_uid=info.st_uid,boot_gid=info.st_gid,boot_mode=stat.S_IMODE(info.st_mode),redux_uid=user.pw_uid,redux_gid=user.pw_gid)


def collect(expected, session_id, *, seconds=2, root=Path('/'), clock=time.time,
            monotonic=time.monotonic, sleep=time.sleep, service=service_probe,
            status_supplier=cached_status, storage=storage_observation, architecture=platform.machine):
    target(expected)
    if not hash_value(session_id) or type(seconds) is not int or not 2<=seconds<=60:
        raise ValueError('bounded explicit acquisition parameters required')
    root=Path(root)
    implementation=collector_digest()
    board=_read(root/'proc/device-tree/model') or ''
    model='pi4' if board.startswith('Raspberry Pi 4 Model') else 'pi5' if board.startswith('Raspberry Pi 5 Model') else None
    revision=_read(root/'usr/share/redux/redux-revision',limit=128)
    boot=_read(root/'proc/sys/kernel/random/boot_id',limit=128)
    if model!=expected['model'] or revision!=expected['revision'] or not boot:
        raise ValueError('local board/source prerequisites unverified')
    uuid.UUID(boot)
    boot_hash=hashlib.sha256(boot.encode()).hexdigest()
    observations=[]
    def add(gate,data,stamp=None):
        observations.append(dict(gate=gate,target=dict(expected),boot_id=boot_hash,
            observed_at=clock() if stamp is None else stamp,data=data))
    # Firmware compatibility is deliberately unavailable here: version strings
    # alone are not approved EEPROM/firmware hashes or tryboot compatibility.
    arch=architecture()
    add('platform',dict(model=model,architecture=arch if arch in ('aarch64','x86_64','armv7l') else 'unknown',firmware_sha256=None,eeprom_sha256=None))
    samples=[];cached={};last_stamp=None
    elapsed=0;started=monotonic()
    while True:
        try:
            cached=status_supplier()
            if not isinstance(cached,dict):raise ValueError('invalid cached status')
            status=cached.get('telemetry')
            if isinstance(status,dict) and number(status.get('measured_at')) and status['measured_at']!=last_stamp:
                values=status.get('values',{});last_stamp=status['measured_at']
                if not isinstance(values,dict):raise ValueError('invalid telemetry values')
                # /proc/stat needs a baseline; never invent its first delta.
                if not (elapsed==0 and values.get('cpu_load_pct') is None):
                    samples.append(dict(measured_at=last_stamp,**{key:values.get(key) if number(values.get(key)) else None for key in
                        ('cpu_temp_c','cpu_load_pct','ram_pct','cpu_frequency_mhz','throttled_mask')}))
        except (OSError,ValueError,TypeError,KeyError):cached={}
        elapsed=max(elapsed,monotonic()-started)
        if elapsed>=seconds:break
        delay=min(2,seconds-elapsed);sleep(delay);elapsed+=delay
    add('resources',dict(samples=samples))
    try:add('storage',storage(root))
    except (OSError,ValueError,KeyError,ImportError):pass  # absent gate remains UNKNOWN
    try:
        units={}
        for unit,expectation in _UNITS.items():
            finding=service(unit,expectation)
            props=finding['evidence']
            if not isinstance(props,dict):raise ValueError('invalid service observation')
            if not all(key in props for key in ('LoadState','ActiveState','UnitFileState')):raise ValueError('unavailable unit')
            if any(v not in ('loaded','masked','active','inactive','failed','enabled','disabled','not-found','activating') for v in props.values()):raise ValueError('unrecognized service properties')
            units[unit]={key:props[key] for key in ('LoadState','ActiveState','UnitFileState')}
        lifecycle=None
        if isinstance(cached.get('runtime'),dict):
            saved=cached['runtime'];stamp=saved.get('updated_utc')
            if number(stamp) and 0<=clock()-stamp<=10:
                value=saved.get('state')
                lifecycle=value if value in ('running','degraded','starting','stopped','recovering','starting_engine','storage_paused','telemetry_degraded','termination_pending','rotating') else None
        worker=cached.get('capture_processing',{})
        if not isinstance(worker,dict):raise ValueError('invalid cached worker')
        scan=worker.get('last_scan')
        outcomes={'ready','deferred','deferred_low_storage','duplicate','error','invalid','skipped','paused_low_storage'}
        if not scan_record_valid(scan) or not set(scan['outcomes'])<=outcomes:scan=None
        elif scan is not None:scan={key:scan[key] for key in ('scanned','completed_utc','outcomes')}
        add('services',dict(units=units,live_state=lifecycle,worker_scan=scan))
    except (OSError,ValueError,TypeError,KeyError):pass
    if (_read(root/'proc/sys/kernel/random/boot_id',limit=128)!=boot
            or _read(root/'usr/share/redux/redux-revision',limit=128)!=revision
            or (_read(root/'proc/device-tree/model') or '')!=board
            or collector_digest()!=implementation):
        raise ValueError('boot changed during acquisition')
    package={'payload':dict(format=FORMAT,target=dict(expected),origin='physical',session_id=session_id,
        collector_sha256=implementation,observed_at=clock(),observations=observations)}
    validate(package)
    return package
