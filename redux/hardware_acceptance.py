"""Offline, bounded hardware evidence evaluation. No device or network I/O.

PASS means an externally trusted collector attests measured primitives for one
authorized session. Signatures authenticate provenance, not physics. Unsigned,
synthetic, historical and out-of-scope observations cannot qualify hardware.
No private identifiers, packets, arbitrary producer messages or paths are emitted.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

from .boot_readiness import parse_mountinfo, mount_check, _UNITS, service_probe
from .core.processing_evidence import count, scan_record_valid
from .core.governor import Governor, Reading, valid_reading
from .core.telemetry import MAX_AGE_SECONDS
from .display.calibration import TouchPoint, evaluate as calibrate

GATES = ('platform', 'storage', 'services', 'radio', 'processing', 'display',
         'resources', 'persistence', 'update_install', 'trial_boot', 'rollback', 'recovery')
UPDATE = frozenset(GATES[8:])
HEX = re.compile(r'[0-9a-f]{64}\Z')
REV = re.compile(r'[0-9a-f]{40}\Z')
FORMAT = 'redux-hardware-observations-v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def number(value):
    return type(value) in (int, float) and abs(value) <= 1e12 and math.isfinite(value)


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or not value.keys() <= set(required) | set(optional):
        raise ValueError('invalid field set')


def hash_value(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def target(value):
    fields(value, ('revision', 'release_sha256', 'device_id', 'model', 'profile'))
    if (not isinstance(value['revision'], str) or not REV.fullmatch(value['revision'])
            or not all(hash_value(value[k]) for k in ('release_sha256', 'device_id'))
            or value['model'] not in ('pi4', 'pi5') or value['profile'] not in ('lite', 'ab')):
        raise ValueError('invalid target identity')


def validate(package):
    # Check nesting before JSON canonicalization or any producer-specific work.
    pending = [(package, 0)]; budget = 0
    while pending:
        value, depth = pending.pop(); budget += 1
        if depth > 12 or budget > 8192:
            raise ValueError('observation structure exceeds budget')
        if isinstance(value, dict):
            if any(not isinstance(k, str) for k in value):
                raise ValueError('invalid JSON key')
            pending.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            pending.extend((v, depth + 1) for v in value)
    if len(canonical(package)) > 128 * 1024:
        raise ValueError('oversized observation envelope')
    fields(package, ('payload',), ('signature',))
    p = package['payload']
    fields(p, ('format', 'target', 'origin', 'session_id', 'collector_sha256', 'observed_at', 'observations'))
    target(p['target'])
    if (p['format'] != FORMAT or p['origin'] not in ('physical', 'synthetic', 'historical')
            or not all(hash_value(p[k]) for k in ('session_id', 'collector_sha256'))
            or not number(p['observed_at']) or p['observed_at'] < 0
            or not isinstance(p['observations'], list) or len(p['observations']) > len(GATES)):
        raise ValueError('invalid observation envelope')
    seen = set()
    boot = None
    for row in p['observations']:
        fields(row, ('gate', 'target', 'boot_id', 'observed_at', 'data'))
        target(row['target'])
        if (row['gate'] not in GATES or row['gate'] in seen or row['target'] != p['target']
                or not hash_value(row['boot_id']) or not number(row['observed_at'])
                or not 0 <= row['observed_at'] <= p['observed_at'] or not isinstance(row['data'], dict)):
            raise ValueError('contradictory/cross-device observation envelope')
        seen.add(row['gate'])
        if row['gate'] not in UPDATE and row['gate'] != 'persistence':
            if boot is not None and boot != row['boot_id']:
                raise ValueError('single-boot observations disagree')
            boot = row['boot_id']
    signature = package.get('signature')
    if signature is not None and (not isinstance(signature, str) or not re.fullmatch(r'[0-9a-f]{128}', signature)):
        raise ValueError('invalid attestation signature encoding')
    return p


def trust(package, policy, expected, now):
    """Trust is supplied separately by the operator, never by the evidence file."""
    p = package['payload']
    if p['target'] != expected:
        return False, 'wrong_target', set()
    if policy is None:
        return False, 'external_authorization_missing', set()
    fields(policy, ('schema', 'target', 'session_id', 'collector_sha256', 'public_key',
                    'authorized_from', 'authorized_until', 'max_age_seconds', 'gate_scope',
                    'firmware_sha256', 'eeprom_sha256'))
    target(policy['target'])
    if (policy['schema'] != 1 or policy['target'] != expected
            or not all(hash_value(policy[k]) for k in ('session_id', 'collector_sha256', 'public_key'))
            or not all(number(policy[k]) for k in ('authorized_from', 'authorized_until', 'max_age_seconds'))
            or not 0 <= policy['authorized_from'] < policy['authorized_until']
            or policy['authorized_until'] - policy['authorized_from'] > 86400
            or not 1 <= policy['max_age_seconds'] <= 86400
            or not isinstance(policy['gate_scope'], list) or len(policy['gate_scope']) > len(GATES)
            or any(k not in GATES for k in policy['gate_scope']) or len(set(policy['gate_scope'])) != len(policy['gate_scope'])):
        raise ValueError('invalid external authorization policy')
    for name in ('firmware_sha256', 'eeprom_sha256'):
        if not isinstance(policy[name], list) or len(policy[name]) > 16 or any(not hash_value(v) for v in policy[name]):
            raise ValueError('invalid compatibility authorization')
    if (p['origin'] != 'physical' or p['session_id'] != policy['session_id']
            or p['collector_sha256'] != policy['collector_sha256']):
        return False, 'origin_or_session_unverified', set()
    if not policy['authorized_from'] <= p['observed_at'] <= now <= policy['authorized_until']:
        return False, 'authorization_expired_or_future', set()
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        return False, 'signature_tool_unavailable', set()
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(policy['public_key'])).verify(
            bytes.fromhex(package.get('signature', '')), canonical(p))
    except (ValueError, InvalidSignature):
        return False, 'signature_unverified', set()
    return True, 'external_session_attestation_verified', set(policy['gate_scope'])


def need_counts(data, names):
    if not all(count(data[k]) for k in names):
        raise ValueError('invalid bounded counter')


def storage(data, _policy, expected, _now):
    fields(data, ('mountinfo', 'label_major_minor', 'boot_uid', 'boot_gid', 'boot_mode', 'redux_uid', 'redux_gid', 'partitions', 'current_slot'))
    if not isinstance(data['mountinfo'], str) or len(data['mountinfo']) > 32768:
        raise ValueError('invalid mount evidence')
    mounts = parse_mountinfo(data['mountinfo'])
    for path, fs, mode in (('/', 'overlay', 'rw'), ('/media/root-ro', 'ext4', 'ro'),
                           ('/boot/firmware', 'vfat', 'ro'), ('/captures', 'ext4', 'rw')):
        if mount_check(mounts, path, fs=fs, mode=mode)['status'] != 'ok':
            return 'FAIL', 'mount_prerequisite_unavailable'
        if {'ro', 'rw'} <= set(mounts[path]['options']):
            return 'FAIL', 'contradictory_mount_modes'
    if (not isinstance(data['label_major_minor'], str) or not re.fullmatch(r'\d{1,5}:\d{1,5}', data['label_major_minor'])
            or mounts['/captures']['device_major_minor'] != data['label_major_minor']
            or data['label_major_minor'] in {mounts['/media/root-ro']['device_major_minor'], mounts['/boot/firmware']['device_major_minor']}):
        return 'FAIL', 'REDUXCAP_identity_mismatch'
    if not isinstance(data['partitions'],list) or len(data['partitions']) != (6 if expected['profile']=='ab' else 3):
        return 'FAIL', 'partition_topology_incomplete'
    parts = {}; devices = set()
    for part in data['partitions']:
        fields(part, ('index','label','uuid','major_minor','disk_major_minor'))
        if (type(part['index']) is not int or not 1 <= part['index'] <= 6 or part['index'] in parts
                or not isinstance(part['label'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{0,20}',part['label'])
                or not isinstance(part['uuid'],str) or not re.fullmatch(r'[a-f0-9-]{8,36}',part['uuid'])
                or not isinstance(part['major_minor'],str) or not re.fullmatch(r'\d{1,5}:\d{1,5}',part['major_minor'])
                or not isinstance(part['disk_major_minor'],str) or not re.fullmatch(r'\d{1,5}:\d{1,5}',part['disk_major_minor'])
                or part['major_minor'] in devices):
            raise ValueError('invalid partition identity')
        parts[part['index']]=part;devices.add(part['major_minor'])
    if len({p['disk_major_minor'] for p in parts.values()}) != 1 or any(p['disk_major_minor'] in devices for p in parts.values()):
        return 'FAIL', 'partition_parent_disk_disagreement'
    if expected['profile']=='ab':
        from boot import ota
        if data['current_slot'] not in ota.SLOTS:
            return 'FAIL', 'running_AB_slot_unknown'
        indices=ota.SLOTS[data['current_slot']]
        ota.validate_topology(parts,data['current_slot'],parts[indices['root']]['uuid'])
        root_index,boot_index,cap_index=indices['root'],indices['boot'],6
        if (mount_check(mounts,'/boot/redux-control',fs='vfat',mode='ro')['status']!='ok'
                or mounts['/boot/redux-control']['device_major_minor']!=parts[1]['major_minor']):
            return 'FAIL', 'AB_control_mount_identity_unverified'
    else:
        if set(parts)!=set((1,2,3)) or data['current_slot'] is not None or parts[3]['label']!='REDUXCAP':
            return 'FAIL', 'lite_partition_identity_mismatch'
        root_index,boot_index,cap_index=2,1,3
    if (mounts['/media/root-ro']['device_major_minor']!=parts[root_index]['major_minor']
            or mounts['/boot/firmware']['device_major_minor']!=parts[boot_index]['major_minor']
            or data['label_major_minor']!=parts[cap_index]['major_minor']):
        return 'FAIL', 'running_mount_partition_identity_mismatch'
    need_counts(data, ('boot_uid', 'boot_gid', 'boot_mode', 'redux_uid', 'redux_gid'))
    good = (0 < data['redux_uid'] < 65536 and 0 < data['redux_gid'] < 65536
            and data['boot_uid'] == data['redux_uid'] and data['boot_gid'] == data['redux_gid'] and data['boot_mode'] == 0o700)
    return ('PASS', 'immutable_mounts_and_boot_ownership_observed') if good else ('FAIL', 'boot_state_ownership_mismatch')


def platform_gate(data, policy, expected, *_):
    fields(data, ('model', 'architecture', 'firmware_sha256', 'eeprom_sha256'))
    if data['model'] != expected['model'] or data['architecture'] != 'aarch64':
        return 'FAIL', 'unsupported_board_or_architecture'
    if not all(hash_value(data[k]) for k in ('firmware_sha256', 'eeprom_sha256')):
        return 'UNKNOWN', 'firmware_measurement_missing'
    if policy is None or any(data[k] not in policy[k] for k in ('firmware_sha256', 'eeprom_sha256')):
        return 'UNKNOWN', 'firmware_compatibility_not_independently_approved'
    return 'PASS', 'model_and_explicitly_approved_firmware_observed'


def service_states(units):
    if not isinstance(units, dict) or set(units) != set(_UNITS):
        raise ValueError('incomplete service observations')
    from types import SimpleNamespace
    for unit, expectation in _UNITS.items():
        props = units[unit]
        fields(props, ('LoadState', 'ActiveState', 'UnitFileState'))
        if any(v not in ('loaded', 'masked', 'active', 'inactive', 'failed', 'enabled', 'disabled', 'not-found', 'activating') for v in props.values()):
            raise ValueError('invalid service enum')
        result = service_probe(unit, expectation, runner=lambda *a, **k: SimpleNamespace(returncode=0,
            stdout='\n'.join(key+'='+val for key, val in props.items())))
        if result['status'] != 'ok':
            return False
    return True


def services(data, _policy, _expected, now):
    fields(data, ('units', 'live_state', 'worker_scan'))
    if not service_states(data['units']):
        return 'FAIL', 'required_service_not_available'
    if data['live_state'] is None:
        return 'UNKNOWN', 'runtime_checkpoint_unavailable_or_stale'
    if data['live_state'] not in ('running','degraded','starting','stopped','recovering','starting_engine',
                                  'storage_paused','telemetry_degraded','termination_pending','rotating'):
        raise ValueError('invalid runtime lifecycle')
    if data['live_state'] in ('stopped','termination_pending'):
        return 'FAIL', 'active_service_contradicts_stopped_runtime'
    if data['live_state'] in ('starting','starting_engine','recovering','storage_paused'):
        return 'UNKNOWN', 'runtime_prerequisites_or_startup_incomplete'
    if not scan_record_valid(data['worker_scan']):
        return 'UNKNOWN', 'worker_startup_incomplete'
    if not 0 <= now - data['worker_scan']['completed_utc'] <= 120:
        return 'UNKNOWN', 'worker_heartbeat_stale'
    return 'PASS', 'headless_services_and_worker_observed'  # degraded radio does not imply failed boot


def radio(data, *_):
    fields(data, ('arphrd', 'pcap_linktype', 'rx_before', 'rx_after', 'tx_before', 'tx_after', 'decoded_rx_frames', 'header_classification'))
    need_counts(data, ('arphrd', 'pcap_linktype', 'rx_before', 'rx_after', 'tx_before', 'tx_after', 'decoded_rx_frames'))
    if data['arphrd'] not in (801, 803) or data['pcap_linktype'] != {801: 105, 803: 127}.get(data['arphrd']):
        return 'FAIL', 'kernel_or_capture_framing_not_native_80211'
    if data['tx_after'] != data['tx_before'] or data['rx_after'] < data['rx_before']:
        return 'FAIL', 'passive_counter_continuity_broken'
    received = data['rx_after'] - data['rx_before']
    if data['decoded_rx_frames'] > received:
        return 'FAIL', 'decoded_reception_exceeds_measured_RX'
    if data['header_classification'] not in ('management', 'control', 'data'):
        return 'FAIL', 'packet_header_validation_failed'
    return ('PASS', 'native_passive_RX_observed') if data['decoded_rx_frames'] > 0 else ('UNKNOWN', 'no_decoded_reception')


def processing(data, _policy, _expected, now):
    fields(data, ('handed_off', 'closed_artifacts', 'converted_artifacts', 'hash_records', 'worker_scan'))
    need_counts(data, ('handed_off', 'closed_artifacts', 'converted_artifacts', 'hash_records'))
    if not 0 <= data['converted_artifacts'] <= data['closed_artifacts'] <= data['handed_off']:
        return 'FAIL', 'contradictory_handoff_conversion_counts'
    if not scan_record_valid(data['worker_scan']) or not 0 <= now - data['worker_scan']['completed_utc'] <= 120:
        return 'UNKNOWN', 'worker_scan_missing_or_stale'
    if data['worker_scan']['outcomes'].get('error', 0):
        return 'FAIL', 'conversion_errors_observed'
    if data['converted_artifacts'] == 0 or data['hash_records'] == 0:
        return 'UNKNOWN', 'no_closed_conversion_observation'
    return 'PASS', 'closed_handoff_and_conversion_observed_integrity_is_separate'


def display(data, *_):
    fields(data, ('framebuffer_width', 'framebuffer_height', 'width', 'height', 'rotation', 'points', 'navigation'))
    if (any(type(data[k]) is not int or not 2 <= data[k] <= 4096 for k in ('framebuffer_width', 'framebuffer_height', 'width', 'height'))
            or type(data['rotation']) is not int or data['rotation'] not in (0, 90, 180, 270)):
        raise ValueError('invalid display geometry')
    expected = (data['framebuffer_height'], data['framebuffer_width']) if data['rotation'] in (90,270) else (data['framebuffer_width'], data['framebuffer_height'])
    if expected != (data['width'], data['height']):
        return 'FAIL', 'display_rotation_geometry_mismatch'
    if not isinstance(data['points'], list) or not 5 <= len(data['points']) <= 32:
        return 'UNKNOWN', 'physical_calibration_samples_missing'
    points = []
    for p in data['points']:
        fields(p, ('raw_x', 'raw_y', 'pixel_x', 'pixel_y'))
        points.append(TouchPoint(**p))
    if calibrate(points, width=data['width'], height=data['height'], tolerance_px=20) is None:
        return 'FAIL', 'inconsistent_touch_mapping'
    if (not isinstance(data['navigation'], list) or len(data['navigation']) > 32
            or any(x not in ('next', 'previous', 'select', 'back', 'scroll') for x in data['navigation'])):
        raise ValueError('invalid navigation observations')
    if not {'next', 'previous', 'select', 'back', 'scroll'} <= set(data['navigation']):
        return 'UNKNOWN', 'navigation_coverage_incomplete'
    return 'PASS', 'geometry_calibration_and_navigation_observed'


def resources(data, _policy, _expected, now):
    fields(data, ('samples',))
    if not isinstance(data['samples'], list) or not 2 <= len(data['samples']) <= 120:
        return 'UNKNOWN', 'measured_load_series_missing'
    last = None; governor = Governor(); observed_load = False
    for sample in data['samples']:
        fields(sample, ('measured_at', 'cpu_temp_c', 'cpu_load_pct', 'ram_pct', 'cpu_frequency_mhz', 'throttled_mask'))
        stamp = sample['measured_at']
        if not number(stamp) or not 0 <= now - stamp <= 600 or last is not None and stamp <= last:
            return 'UNKNOWN', 'sensor_series_stale_or_nonmonotonic'
        reading = Reading(**{k:sample[k] for k in ('cpu_temp_c', 'cpu_load_pct', 'ram_pct')})
        clean = valid_reading(reading)
        if any(getattr(clean, k) is None for k in ('cpu_temp_c', 'cpu_load_pct', 'ram_pct')) or not number(sample['cpu_frequency_mhz']) or not 1 <= sample['cpu_frequency_mhz'] <= 10000:
            return 'UNKNOWN', 'resource_measurements_unavailable'
        if type(sample['throttled_mask']) is not int or not 0 <= sample['throttled_mask'] <= 0xffffffff:
            return 'UNKNOWN', 'power_flags_unavailable'
        if sample['throttled_mask'] & ~0x000f000f:
            return 'UNKNOWN', 'unsupported_firmware_flags'
        if sample['throttled_mask']:
            return 'FAIL', 'throttling_or_undervoltage_observed'
        decision = governor.evaluate(clean, stamp)
        if decision.mode.value == 'survival' and any(d.startswith('cpu_temp ') for d in decision.drivers):
            return 'FAIL', 'governor_survival_temperature_observed'
        observed_load |= clean.cpu_load_pct >= 50
        last = stamp
    if now - last > MAX_AGE_SECONDS:
        return 'UNKNOWN', 'latest_sensor_sample_stale'
    if not observed_load or last - data['samples'][0]['measured_at'] < 30:
        return 'UNKNOWN', 'measured_load_duration_insufficient'
    return 'PASS', 'bounded_measured_load_series_observed_battery_unavailable'


def persistence(data, _policy, _expected, now):
    fields(data, ('before_boot', 'after_boot', 'before_storage', 'after_storage', 'before_inventory', 'after_inventory', 'before_count', 'after_count', 'service_observations', 'before_at', 'after_at'))
    if not all(hash_value(data[k]) for k in ('before_boot','after_boot','before_storage','after_storage','before_inventory','after_inventory')):
        raise ValueError('invalid persistence identity')
    need_counts(data, ('before_count','after_count'))
    if not all(number(data[k]) for k in ('before_at','after_at')) or not max(0,now-86400) <= data['before_at'] < data['after_at'] <= now:
        return 'FAIL', 'persistence_chronology_invalid'
    if data['before_boot'] == data['after_boot']:
        return 'UNKNOWN', 'different_boot_not_observed'
    if data['before_storage'] != data['after_storage'] or data['before_inventory'] != data['after_inventory'] or data['before_count'] != data['after_count']:
        return 'FAIL', 'capture_preservation_mismatch'
    if not service_states(data['service_observations']):
        return 'FAIL', 'persistent_service_recovery_not_observed'
    if data['before_count'] == 0:
        return 'UNKNOWN', 'recovery_or_nonempty_preservation_unverified'
    return 'PASS', 'distinct_boots_and_preserved_existing_inventory_observed'


def recovery(data, policy, expected, now):
    # Recovery media is independent of a successful automatic fallback trace.
    extra=('medium_sha256','restored_revision','restored_release_sha256','bundle_sha256')
    if any(k not in data for k in extra):
        return 'UNKNOWN', 'independent_recovery_medium_observations_missing'
    base={k:v for k,v in data.items() if k not in extra}
    status,reason=persistence(base,policy,expected,now)
    if status!='PASS':return status,reason
    if (data['medium_sha256']!=expected['release_sha256'] or data['restored_release_sha256']!=expected['release_sha256']
            or data['restored_revision']!=expected['revision'] or not hash_value(data['bundle_sha256'])):
        return 'FAIL', 'recovery_medium_or_restored_release_mismatch'
    return 'PASS', 'separate_recovery_medium_and_preservation_observed'


def update(data, _policy, _expected, now, gate):
    from boot import ota
    fields(data, ('initial_slot', 'events', 'bundle_sha256', 'captures_before', 'captures_after'))
    if data['initial_slot'] not in ota.SLOTS or not all(hash_value(data[k]) for k in ('bundle_sha256','captures_before','captures_after')):
        raise ValueError('invalid update identity')
    if data['captures_before'] != data['captures_after']:
        return 'FAIL', 'update_capture_preservation_mismatch'
    if not isinstance(data['events'], list) or not 1 <= len(data['events']) <= 16:
        return 'UNKNOWN', 'update_trace_missing'
    state = ota.initial_state(data['initial_slot']); stable = data['initial_slot']; staged = trial_seen = committed = rolled_back = False
    boots = {}; last = -1
    for event in data['events']:
        fields(event, ('at', 'boot_id', 'current', 'trial', 'action', 'slot', 'value'))
        if not number(event['at']) or not max(0, now - 86400) <= event['at'] <= now or event['at'] <= last or not hash_value(event['boot_id']) or event['current'] not in ota.SLOTS or type(event['trial']) is not bool:
            raise ValueError('invalid update chronology')
        last = event['at']
        boot_state = (event['current'],event['trial'])
        if event['boot_id'] in boots and boots[event['boot_id']] != boot_state:
            raise ValueError('one boot claims conflicting running slots')
        boots[event['boot_id']] = boot_state
        if event['action'] not in ('set-primary','set-state'):
            raise ValueError('unsupported update event')
        trial_seen |= event['trial'] and event['current'] == state['pending'] and event['current'] != stable
        state, selector = ota.transition(state, stable, event['current'], event['action'], event['slot'], event['value'], trial=event['trial'])
        staged |= event['action'] == 'set-primary'
        committed |= selector is not None
        rolled_back |= staged and state['pending'] is None and event['current'] == stable and trial_seen and not committed and event['action']=='set-state' and event['value']=='good'
        if selector is not None:stable = selector
    if gate == 'update_install':good = staged
    elif gate == 'trial_boot':good = trial_seen and committed and len(boots) >= 2
    else:good = rolled_back and len(boots) >= 3
    return ('PASS', 'backend_consistent_observed_' + gate) if good else ('UNKNOWN', 'staging_or_abandoned_trial_is_not_proven_' + gate)


EVALUATORS = dict(platform=platform_gate, storage=storage, services=services, radio=radio,
                  processing=processing, display=display, resources=resources, persistence=persistence,recovery=recovery)


def evaluate(package, *, expected, now, policy=None, gates=None, authenticated_bundle_sha256=None):
    target(expected)
    if not number(now) or now < 0:
        raise ValueError('invalid evaluation clock')
    p = validate(package)
    trusted, reason, scope = trust(package, policy, expected, now)
    selected = GATES if gates is None else tuple(gates)
    if not selected or any(g not in GATES for g in selected) or len(set(selected)) != len(selected):
        raise ValueError('invalid gate selection')
    rows = {row['gate']: row for row in p['observations']}
    results = []
    for gate in selected:
        row = rows.get(gate); age = now - row['observed_at'] if row else None
        status, detail = 'UNKNOWN', 'observation_missing'
        observation_status = 'UNKNOWN'
        if p['target'] != expected:
            status, detail = 'FAIL', 'wrong_target'
            observation_status = 'FAIL'
        elif expected['profile'] == 'lite' and gate in UPDATE:
            status, detail = 'NOT APPLICABLE', 'lite_has_no_AB_backend'
            if row is not None:
                status, detail = 'FAIL', 'AB_observation_contradicts_lite_profile'
        elif row is not None:
            try:
                if gate in UPDATE and gate!='recovery':
                    status, detail = update(row['data'], policy, expected, now, gate)
                else:
                    status, detail = EVALUATORS[gate](row['data'], policy, expected, now)
            except (ValueError, TypeError, KeyError, OverflowError):
                status, detail = 'FAIL', 'malformed_measurement'
            if gate in ('persistence','recovery') and row['data'].get('after_boot') != row['boot_id']:
                status, detail = 'FAIL', 'persistence_boot_identity_mismatch'
            if gate in UPDATE and gate!='recovery' and isinstance(row['data'].get('events'),list) and row['data']['events']:
                events = row['data']['events']
                if (any(not isinstance(e,dict) for e in events) or events[-1].get('boot_id') != row['boot_id'] or any(not number(e.get('at')) or e['at'] > row['observed_at'] for e in events)):
                    status, detail = 'FAIL', 'update_observation_identity_or_time_mismatch'
            if gate=='resources' and isinstance(row['data'].get('samples'),list) and any(
                    isinstance(s,dict) and number(s.get('measured_at')) and s['measured_at']>row['observed_at'] for s in row['data']['samples']):
                status,detail='FAIL','sensor_timestamp_exceeds_observation'
            if gate in ('services','processing') and isinstance(row['data'].get('worker_scan'),dict):
                stamp=row['data']['worker_scan'].get('completed_utc')
                if number(stamp) and stamp>row['observed_at']:
                    status,detail='FAIL','worker_timestamp_exceeds_observation'
            observation_status = status
            if age < 0 or age > (policy['max_age_seconds'] if policy else 300):
                status, detail = 'UNKNOWN', 'observation_stale_or_future'
            elif status == 'PASS' and (not trusted or gate not in scope):
                status, detail = 'UNKNOWN', reason if not trusted else 'gate_not_authorized'
            elif status == 'PASS' and policy and (row['observed_at'] < policy['authorized_from']
                    or gate in UPDATE-{'recovery'} and any(e['at'] < policy['authorized_from'] for e in row['data']['events'])
                    or gate=='resources' and any(s['measured_at'] < policy['authorized_from'] for s in row['data']['samples'])
                    or gate in ('persistence','recovery') and row['data']['before_at'] < policy['authorized_from']):
                status, detail = 'UNKNOWN', 'measurement_precedes_authorized_session'
            elif status == 'PASS' and gate in UPDATE and (not hash_value(authenticated_bundle_sha256)
                    or row['data'].get('bundle_sha256') != authenticated_bundle_sha256):
                status, detail = 'UNKNOWN', 'independent_bundle_authentication_missing'
        results.append(dict(gate=gate, status=status, reason=detail, age_seconds=age,
                            observation_status=observation_status if row is not None and not (expected['profile']=='lite' and gate in UPDATE) else status,
                            provenance_verified=trusted and gate in scope))
    # A collector's events cannot establish production bundle authenticity.
    # The CLI attaches freshly executed candidate checks separately.
    return dict(schema=1, target=expected, evidence_origin=p['origin'],
                evidence_sha256=hashlib.sha256(canonical(package)).hexdigest(),
                evaluated_at=now, provenance=reason, matrix=results,
                result='FAIL' if any(r['status']=='FAIL' for r in results) else 'UNKNOWN' if any(r['status']=='UNKNOWN' for r in results) else 'PASS',
                physical_attestation_complete=set(selected)==set(GATES) and all(r['status'] in ('PASS','NOT APPLICABLE') for r in results),
                production_ready=False, note='External attestation is not independent physical certification; release authenticity and operator review remain separate.')
