"""Offline RAUC readiness and recovery inspection. Never discover or mutate a device."""
from __future__ import annotations
import configparser
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat

from boot import ota

REVISION = re.compile(r'[0-9a-f]{40}')


def report(checks, **extra):
    states = [row['status'] for row in checks]
    return dict(schema=1, checks=checks, result='FAIL' if 'FAIL' in states else 'UNKNOWN' if 'UNKNOWN' in states else 'PASS',
                automatic_rollback_verified=False, physical_boot_verified=False, **extra)


def check(name, status, detail):
    return dict(name=name, status=status, detail=detail)


def safe_path(root, name, *, allow_leaf_link=False):
    """Guest paths are anchored to a real snapshot; never follow host links."""
    root = Path(root).absolute()
    if any(part == '..' for part in Path(root).parts):
        raise ValueError('snapshot path traversal')
    for folder in (root, *root.parents):
        if folder.is_symlink():
            raise ValueError('snapshot directory symlink')
    guest = PurePosixPath(name)
    if guest.is_absolute() or not guest.parts or any(p in ('.', '..', '') for p in name.split('/')) or '\x00' in name:
        raise ValueError('unsafe relative snapshot path')
    location = root
    for index, part in enumerate(guest.parts):
        location = location / part
        if location.is_symlink() and not (allow_leaf_link and index == len(guest.parts)-1):
            raise ValueError('snapshot file/directory symlink')
    return location


def read(root, name, limit=65536):
    path = safe_path(root, name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError('unsafe or oversized snapshot file: ' + name)
        value = stream.read(limit+1)
        after = os.fstat(stream.fileno())
        identity = lambda s: (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if len(value) > limit or identity(before) != identity(after):
            raise ValueError('snapshot changed during inspection')
    return value



def snapshot_file(source,destination,*,limit=64*1024*1024*1024):
    """Copy a stable regular artifact once; tools inspect only the private copy."""
    source,destination=Path(source),Path(destination)
    safe_path(source.parent,source.name)
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<before.st_size<=limit:
            raise ValueError('artifact must be a bounded singly-linked regular file')
        if before.st_size>shutil.disk_usage(destination.parent).free:
            raise ValueError('insufficient private snapshot space')
        with destination.open('xb') as output:
            remaining=before.st_size
            while remaining:
                chunk=stream.read(min(1024*1024,remaining))
                if not chunk:
                    raise ValueError('artifact truncated during snapshot')
                output.write(chunk);remaining-=len(chunk)
            if stream.read(1):
                raise ValueError('artifact grew during snapshot')
        after=os.fstat(stream.fileno())
        identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if identity(before)!=identity(after) or identity(before)!=identity(source.lstat()):
            raise ValueError('artifact changed during snapshot')
    return destination

def json_record(raw):
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError('duplicate JSON field')
            result[name] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    if not isinstance(value, dict):
        raise ValueError('snapshot JSON must be an object')
    return value


def ini(text):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ValueError('malformed configuration') from exc
    if parser.defaults():
        raise ValueError('configuration DEFAULT inheritance is unsupported')
    return parser


def public_keyring(raw, *, now=None):
    from cryptography import x509
    if b'PRIVATE KEY' in raw:
        raise ValueError('private key material is forbidden in an image keyring')
    certs = x509.load_pem_x509_certificates(raw)
    if not certs:
        raise ValueError('public certificate keyring is empty')
    current = now or datetime.now(timezone.utc)
    for cert in certs:
        if not cert.not_valid_before_utc <= current <= cert.not_valid_after_utc:
            raise ValueError('update trust certificate is not currently valid')
    return hashlib.sha256(raw).hexdigest()


def validate_configuration(text, context):
    ota.validate_runtime_context(context, 'root=PARTUUID=' + context['devices'][str(ota.SLOTS[context['current']]['root'])].split('/')[-1] +
                                 ' rauc.slot=' + context['current'])
    actual = ini(text)
    expected = ini(ota.system_conf(context))
    if actual.get('system', 'bootloader', fallback='') != 'custom':
        raise NotImplementedError('only the Redux custom tryboot backend is supported')
    if actual.get('system', 'compatible', fallback='') != ota.COMPATIBLE:
        raise ValueError('RAUC compatible does not match Redux A/B revision')
    if set(actual.sections()) != set(expected.sections()):
        raise ValueError('RAUC must contain exactly the paired boot/rootfs slots and standard handlers')
    for section in expected.sections():
        if dict(actual[section]) != dict(expected[section]):
            raise ValueError('unsafe RAUC configuration section: ' + section)
    return {'compatible': ota.COMPATIBLE, 'slots': ['rootfs.0','boot.0','rootfs.1','boot.1']}


def inspect_configuration(root, *, revision, context=None, now=None):
    checks = []
    condition = 'unverified'
    try:
        if not isinstance(revision,str) or not REVISION.fullmatch(revision):
            raise ValueError('expected release revision must be a complete lowercase SHA')
        actual = read(root,'usr/share/redux/redux-revision',256).decode('ascii').strip()
        if actual != revision:
            raise ValueError('installed release identity differs from requested revision')
        checks.append(check('Release identity','PASS','baked source revision matches'))
        config = safe_path(root,'etc/rauc/system.conf',allow_leaf_link=True)
        mask = safe_path(root,'etc/systemd/system/rauc.service',allow_leaf_link=True)
        if not config.exists() and not config.is_symlink():
            if mask.is_symlink() and os.readlink(mask) == '/dev/null':
                checks.append(check('RAUC configuration','UNKNOWN','intentionally masked; no update slots configured'))
                return report(checks, condition='unconfigured', update_permitted=False)
            raise ValueError('RAUC configuration missing without an explicit disabled-service mask')
        if mask.exists() or mask.is_symlink():
            if mask.is_symlink() and os.readlink(mask) == '/dev/null':
                raise ValueError('configured RAUC is simultaneously masked')
        if config.is_symlink():
            if os.readlink(config) != '/run/redux-ota/system.conf':
                raise ValueError('RAUC config link targets an unexpected path')
            if context is None:
                checks.append(check('Generated slot topology','UNKNOWN','runtime PARTUUID context not supplied; no host/device discovery attempted'))
            else:
                # A factory snapshot normally has no /run. Validate the exact
                # generator policy, without manufacturing a live runtime file.
                generated = safe_path(root,'run/redux-ota/system.conf')
                text = read(root,'run/redux-ota/system.conf').decode() if generated.exists() else ota.system_conf(context)
                validate_configuration(text,context)
                checks.append(check('Generated slot topology','PASS','paired boot/rootfs PARTUUID policy matches six-partition context'))
        else:
            if context is None:
                raise ValueError('static RAUC configuration requires explicit partition context')
            validate_configuration(read(root,'etc/rauc/system.conf').decode(),context)
            checks.append(check('Slot topology','PASS','exact supported configuration and unique partition identities'))
        generator=safe_path(root,'usr/lib/systemd/system-generators/redux-ota',allow_leaf_link=True)
        if not generator.is_symlink() or os.readlink(generator)!='/usr/local/libexec/redux-rauc-generator':
            raise ValueError('RAUC systemd generator registration missing or modified')
        wrapper=read(root,'usr/local/bin/redux-ota',8192).decode()
        if wrapper!='#!/bin/sh\nexport PYTHONPATH=/opt/redux\nexec /usr/bin/python3 /opt/redux/boot/ota.py "$@"\n':
            raise ValueError('OTA entrypoint wrapper missing or modified')
        import root_payload
        for name,(group,*_) in root_payload._AB_UNITS.items():
            safe_path(root,'etc/systemd/system/'+group+'/'+name,allow_leaf_link=True)
        root_payload._enabled_unit_contract(Path(root),root_payload._AB_UNITS)
        fingerprint = public_keyring(read(root,'etc/rauc/keyring.pem'),now=now)
        checks.append(check('Update trust keyring','PASS','valid public certificates; SHA256 ' + fingerprint))
        for mode in ('backend','preinstall','health','deadline','generator'):
            path = 'usr/local/libexec/redux-rauc-' + mode
            body = read(root,path,8192).decode()
            expected = '#!/bin/sh\nexport PYTHONPATH=/opt/redux\nexec /usr/bin/python3 /opt/redux/boot/ota.py ' + mode + ' "$@"\n'
            if body != expected or not safe_path(root,path).stat().st_mode & 0o111:
                raise ValueError('missing/modified RAUC helper: ' + mode)
        if read(root,'opt/redux/boot/ota.py',131072) != (Path(__file__).resolve().parents[1]/'boot/ota.py').read_bytes():
            raise NotImplementedError('installed OTA generator/backend differs from the inspected source policy')
        for name in ('redux-ota-health.service','redux-ota-deadline.service','redux-ota-deadline.timer'):
            # Exact packaged service policy prevents duplicate directives or
            # widened executable commands being mistaken for bounded rollback.
            reference = Path(__file__).parent/'stage-redux/50-rauc/files'/name
            if read(root,'etc/systemd/system/'+name) != reference.read_bytes():
                raise ValueError('OTA service policy mismatch: ' + name)
        dropin = read(root,'etc/systemd/system/rauc.service.d/redux.conf')
        if dropin != (Path(__file__).parent/'stage-redux/50-rauc/files/rauc.conf').read_bytes():
            raise ValueError('RAUC writable status mount ordering missing or modified')
        watchdog=ini(read(root,'etc/systemd/system.conf.d/redux-watchdog.conf').decode())
        if dict(watchdog['Manager'])!={'watchdogdevice':'/dev/watchdog0','runtimewatchdogsec':'15s','rebootwatchdogsec':'15s'}:
            raise ValueError('PID 1 watchdog policy disabled or modified')
        config=read(root,'boot/firmware/config.txt',8192).decode()
        if re.findall(r'(?m)^kernel_watchdog_timeout=(\d+)$',config)!=['60']:
            raise ValueError('kernel handover watchdog policy missing/ambiguous')
        checks.append(check('Backend and recovery services','PASS','bounded health/deadline policy present; execution not verified'))
        condition = 'configured' if context is not None else 'unverified'
        checks.append(check('Firmware rollback acceptance','UNKNOWN','EEPROM GPT/tryboot, watchdog reset and selector power-loss behavior require Pi 4/5 trials'))
    except NotImplementedError as exc:
        condition = 'unsupported'
        checks.append(check('RAUC supported profile','FAIL',str(exc)))
    except (OSError,ValueError,KeyError,TypeError,UnicodeError,configparser.Error) as exc:
        checks.append(check('RAUC safety invariants','FAIL',str(exc)))
    except ImportError:
        checks.append(check('Certificate validation','UNKNOWN','install the local cryptography extra; no trust validation performed'))
    return report(checks,condition=condition,update_permitted=False)


def recovery_plan(root, *, cmdline_name='proc/cmdline', trial=None):
    """Inspect saved metadata only. Actions are descriptions, never commands."""
    checks, actions = [], []
    phase, stable, current, pending = 'indeterminate', None, None, None
    try:
        selector = read(root,'boot/redux-control/autoboot.txt',4096).decode()
        stable = ota.stable_from_autoboot(selector)
        current = ota.slot_from_cmdline(read(root,cmdline_name,8192).decode())
        context = json_record(read(root,'run/redux-ota/context.json',8192))
        ota.validate_runtime_context(context,read(root,cmdline_name,8192).decode())
        # Missing ledger is NOT proof of a factory boot in a post-failure snapshot.
        json_record(read(root,'captures/rauc/boot-state.json',16384))
        state = ota.load_state(safe_path(root,'captures/rauc/boot-state.json'),stable)
        pending = state['pending']
        checks.append(check('Saved boot state','PASS','selector, kernel slot, context and ledger are structurally consistent'))
        if current != stable:
            if trial is not True or pending != current:
                raise ValueError('non-default running slot lacks an independently observed matching trial')
            phase = 'candidate-trial'
            actions = ['Preserve the committed '+stable+' selector; do not mark the candidate good from offline evidence.',
                       'Obtain current health/watchdog and bootloader trial evidence before any separately approved recovery.']
        elif pending == stable:
            phase = 'interrupted-commit'
            # Reuse the actual backend state machine to prove this reconciliation
            # preserves the selector. Do not persist the simulated result.
            _, change = ota.transition(state,stable,current,'set-state',current,'good')
            if change is not None:
                raise ValueError('offline reconciliation unexpectedly changes the selector')
            actions = ['Selector commit is recorded but ledger acknowledgement is incomplete; preserve both volumes.',
                       'After independently verifying the current boot, review the existing health reconciliation; do not rewrite the selector.']
        elif pending is not None:
            phase = 'staged-or-fallback'
            actions = ['The stable slot is current with a pending alternate: staging and completed rollback cannot be distinguished from this snapshot.',
                       'Preserve captures and the stable selector; obtain fresh trial/install evidence before choosing a new attempt.']
        else:
            phase = 'stable'
            actions = ['No trial is recorded. This does not prove inactive-slot integrity or a completed rollback.',
                       'Retain a separately verified recovery medium and preserve capture data.']
        checks.append(check('Recovery execution','UNKNOWN','offline plan only; snapshot age, boot success and actual rollback are unverified'))
    except (OSError,ValueError,TypeError,KeyError,UnicodeError) as exc:
        phase = 'blocked'
        checks.append(check('Recovery evidence','FAIL',str(exc)))
        actions = ['Do not install, commit a slot or rewrite boot metadata from contradictory/incomplete evidence.',
                   'Preserve the original medium and captures; inspect a separately acquired read-only snapshot or recovery medium.']
    return report(checks,phase=phase,stable=stable,current=current,pending=pending,
                  actions=actions,execution_permitted=False)
