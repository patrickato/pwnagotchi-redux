"""Session organization around existing read-only probes, never qualification.

Host planning is offline. Acquisition is an explicit, separately authorized
operation; no service, radio, input, partition or installed-state changes.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import time

FORMAT = 'redux-bringup-session-v1'


def token(value, pattern=r'[A-Za-z0-9_.+-]{1,100}'):
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else None


def capture_progress(status, now):
    """Only cached aggregate producer facts; paths/events never prove artifacts."""
    from .core.processing_evidence import scan_record_valid, status_counts_valid
    worker = status.get('capture_processing')
    if not isinstance(worker, dict):
        return {'available': False, 'fresh': False, 'artifact_count': None, 'scan': None}
    scan = worker.get('last_scan')
    allowed = {'ready','deferred','deferred_low_storage','duplicate','error','invalid','skipped','paused_low_storage','storage_error'}
    scan = {k: scan[k] for k in ('scanned', 'completed_utc', 'outcomes')} if scan_record_valid(scan) and set(scan['outcomes']) <= allowed else None
    counts = worker.get('by_status')
    valid_counts = status_counts_valid(worker) and isinstance(counts, dict) and set(counts) <= allowed
    return {'available': worker.get('available') is True,
            'fresh': scan is not None and 0 <= now - scan['completed_utc'] <= 120,
            'artifact_count': worker.get('artifacts') if valid_counts else None,
            'scan': scan, 'by_status': counts if valid_counts else None}


def remote_snapshot(peer, session_id, host_revision, *, root=Path('/'), clock=time.time,
                    sleep=time.sleep, status_supplier=None, radio_supplier=None,
                    display_supplier=None, read=None, runner=None, sanitizer=None):
    """One shared cached-status sample, one radio inventory, two RX reads."""
    from .boot_readiness import _read, parse_mountinfo, mount_check
    from .radio.local_evidence import BoundedReader, collect, read_small, Unavailable
    from .acceptance import report
    from .display.acceptance import inventory, panel_readiness
    if not token(session_id, r'[a-f0-9]{64}') or not token(host_revision, r'[a-f0-9]{40}'):
        raise ValueError('invalid session identity')
    read = read or _read
    root = Path(root)
    def read_at(name, limit=8192):
        return read(root / name, limit=limit)
    started = clock()
    revision = token(read_at('usr/share/redux/redux-revision', 128), r'[a-f0-9]{40}')
    boot = read_at('proc/sys/kernel/random/boot_id', 128)
    board = read_at('proc/device-tree/model', 128)
    model = 'pi4' if isinstance(board, str) and board.startswith('Raspberry Pi 4 Model') else 'pi5' if isinstance(board, str) and board.startswith('Raspberry Pi 5 Model') else None
    boot_token = token(boot, r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
    class SessionReader(BoundedReader):
        @staticmethod
        def allowed(args):
            return BoundedReader.allowed(args) or tuple(args) in {
                ('/usr/bin/vcgencmd', 'version'), ('/usr/bin/vcgencmd', 'bootloader_version')}
    run = runner or SessionReader(seconds=35)
    firmware = {}
    for label, command in (('firmware', 'version'), ('eeprom', 'bootloader_version')):
        try:
            value = run(['/usr/bin/vcgencmd', command])
            firmware[label + '_version_sha256'] = hashlib.sha256(value.encode()).hexdigest() if value.strip() else None
        except (OSError, ValueError, Unavailable):
            firmware[label + '_version_sha256'] = None
    firmware['compatibility'] = 'UNKNOWN'
    rx = []
    radio_names = []
    def observed_radio(management_peer, **kwargs):
        receipt = collect(management_peer, **kwargs)
        before = []
        for row in receipt['radios']:
            interface = row['iface']
            radio_names.append(interface)
            base = root / 'sys/class/net' / interface
            before.append((base, read_small(base/'ifindex'), read_small(base/'type'),
                           int(read_small(base/'statistics/rx_packets')), int(read_small(base/'statistics/tx_packets'))))
        stamp = clock()
        sleep(1)
        for ordinal, (base, index, kind, received, sent) in enumerate(before, 1):
            after = (read_small(base/'ifindex'), read_small(base/'type'), int(read_small(base/'statistics/rx_packets')), int(read_small(base/'statistics/tx_packets')))
            stable = (index, kind) == after[:2] and after[2] >= received and after[3] >= sent
            rx.append({'radio': ordinal, 'identity_stable': stable, 'arphrd': int(kind),
                       'rx_delta': after[2]-received if stable else None,
                       'tx_delta': after[3]-sent if stable else None, 'started_at': stamp,
                       'finished_at': clock(), 'decoded_reception': 'UNKNOWN'})
        return receipt
    try:
        radios = (radio_supplier or report)(peer, run=run, collector=observed_radio)
        if radios.get('inventory_status') not in (None, 'fresh'):
            rx = []
    except (OSError, ValueError, TypeError, KeyError, Unavailable):
        radios = {'inventory_status': 'unavailable', 'gates': {}, 'radios': []}
        rx = []
    try:
        cached = status_supplier() if status_supplier else {}
        status = sanitizer(cached, clock()) if sanitizer else {}
        runtime = cached.get('runtime') if isinstance(cached.get('runtime'), dict) else {}
        selected = runtime.get('iface')
        for row, name in zip(rx, radio_names):
            row['selected'] = selected == name if isinstance(selected, str) and selected in radio_names else None
        processing = capture_progress(cached, clock())
        plugins = cached.get('plugins')
        plugin_inventory = {'available': isinstance(plugins, dict) and plugins.get('availability') == 'inspected',
                            'runtime_activation': 'UNKNOWN'}
    except (OSError, ValueError, TypeError, KeyError):
        status, processing, plugin_inventory = {}, {'available': False, 'fresh': False}, {'available': False, 'runtime_activation': 'UNKNOWN'}
    try:
        mountinfo = read_at('proc/self/mountinfo', 131072)
        if not mountinfo:
            raise ValueError('mount inventory unavailable')
        mounts = parse_mountinfo(mountinfo)
        storage = {}
        for name, path, fs, mode in (('root','/','overlay','rw'),('root_lower','/media/root-ro','ext4','ro'),('boot','/boot/firmware','vfat','ro'),('captures','/captures','ext4','rw')):
            result = mount_check(mounts, path, fs=fs, mode=mode,
                                 label='REDUXCAP' if name == 'captures' else None,
                                 label_path=root/'dev/disk/by-label/REDUXCAP')
            storage[name] = {'status': result['status'], 'device_major_minor': token(mounts.get(path,{}).get('device_major_minor'), r'[0-9]{1,5}:[0-9]{1,8}'), 'filesystem': mounts.get(path,{}).get('filesystem') if mounts.get(path,{}).get('filesystem') in ('overlay','ext4','vfat') else None}
        storage['available_bytes'] = None
        if storage['captures']['status'] == 'ok':
            try:
                info = os.statvfs(root/'captures')
                storage['available_bytes'] = info.f_bavail * info.f_frsize
            except OSError:
                pass
    except (OSError, ValueError, KeyError):
        storage = {'availability': 'unknown'}
    try:
        devices = (display_supplier or inventory)()
        display = panel_readiness(devices)
        display['framebuffers'] = [{
            'ordinal': i + 1, 'dimensions': token(row.get('virtual_size'), r'[0-9]{1,5},[0-9]{1,5}'),
            'bits_per_pixel': token(row.get('bits_per_pixel'), r'[0-9]{1,3}'),
            'stride': token(row.get('stride'), r'[0-9]{1,8}'),
            'driver': token(row.get('driver'), r'[A-Za-z0-9_-]{1,40}')}
            for i,row in enumerate(devices.get('framebuffers', [])[:64]) if isinstance(row, dict)]
    except (OSError, ValueError, TypeError):
        display = {'state': 'UNAVAILABLE', 'physical_display_verified': False, 'touch_verified': False}
    unchanged = (boot == read_at('proc/sys/kernel/random/boot_id', 128)
                 and revision == token(read_at('usr/share/redux/redux-revision', 128), r'[a-f0-9]{40}')
                 and board == read_at('proc/device-tree/model', 128))
    return {'format': FORMAT, 'session_id': session_id, 'host_revision': host_revision,
            'embedded_revision': revision, 'model': model, 'kernel': token(platform.release()),
            'architecture': token(platform.machine()), 'firmware': firmware, 'boot_pseudonym': hashlib.sha256(boot_token.encode()).hexdigest() if boot_token else None,
            'observed_at': started, 'completed_at': clock(), 'identity_stable': unchanged and boot_token is not None,
            'collector_provenance': 'host source in process memory; installed revision independently read',
            'hardware_changes': False, 'unsigned': True, 'radio': radios, 'rx_observations': rx,
            'status': status, 'processing': processing, 'plugins': plugin_inventory,
            'storage': storage, 'display': display,
            'physical_acceptance': 'UNKNOWN', 'release_identity': 'UNKNOWN',
            'card_identity': 'UNKNOWN', 'update_rollback': 'UNKNOWN'}
