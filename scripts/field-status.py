#!/usr/bin/env python3
"""Bounded read-only field status; only allowlisted measurements leave the Pi."""
from __future__ import annotations
import argparse
import inspect
import ipaddress
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys


def sanitize(data, now):
    """Pure shared remote sanitizer. No paths, free text, identifiers or reasons."""
    def number(value, low=0, high=1e15):
        try:
            return value if type(value) in (int, float) and math.isfinite(value) and low <= value <= high else None
        except OverflowError:
            return None
    def state(value):
        return value if value in ('ok', 'attention', 'degraded', 'action', 'unknown') else None
    if not isinstance(data, dict):
        raise ValueError('invalid status root')
    doctor = data.get('doctor') if isinstance(data.get('doctor'), dict) else {}
    findings = doctor.get('findings') if isinstance(doctor.get('findings'), list) else []
    areas = {}
    for item in findings[:100]:
        if isinstance(item, dict) and item.get('area') in (
                'thermal/power', 'radio', 'capture', 'location', 'scope', 'live engine', 'native RF link type'):
            severity = state(item.get('status'))
            if severity:
                areas[item['area']] = severity
    runtime = data.get('runtime') if isinstance(data.get('runtime'), dict) else {}
    updated = number(runtime.get('updated_utc'))
    age = now - updated if updated is not None else None
    freshness = ('fresh' if 0 <= age <= 12 else 'future' if age < 0 else 'stale') if age is not None else 'invalid'
    acceptance = {'checkpoint_freshness': freshness}
    if runtime.get('state') in ('running', 'starting', 'starting_engine', 'rotating',
            'degraded', 'telemetry_degraded', 'storage_paused', 'stopped', 'lease_blocked', 'termination_pending'):
        acceptance['live_state'] = runtime['state']
    if type(runtime.get('dashboard_active')) is bool:
        acceptance['dashboard'] = str(runtime['dashboard_active'])
    for key in ('events_seen', 'handed_off', 'capture_link_type'):
        value = runtime.get(key)
        if type(value) is int and 0 <= value <= 2**63-1:
            acceptance[key] = str(value)
    out = {'doctor_overall': state(doctor.get('overall')), 'areas': areas,
           'telemetry': None, 'acceptance': acceptance}
    candidates = [(key, data.get(key)) for key in ('telemetry', 'hardware_telemetry', 'resource_telemetry')]
    candidates += [('runtime.' + key, runtime.get(key)) for key in ('telemetry', 'hardware_telemetry', 'resource_telemetry')]
    for key, obj in candidates:
        if not isinstance(obj, dict) or not isinstance(obj.get('values'), dict):
            continue
        values = obj['values']
        limits = {'cpu_temp_c': (-20, 150), 'cpu_load_pct': (0, 100), 'ram_pct': (0, 100),
                  'cpu_frequency_mhz': (1, 10000), 'battery_pct': (0, 100)}
        clean = {name: number(values.get(name), *bounds) for name, bounds in limits.items()}
        mask = values.get('throttled_mask')
        clean['throttled_mask'] = mask if type(mask) is int and 0 <= mask <= 0xffffffff else None
        clean['on_battery'] = values.get('on_battery') if type(values.get('on_battery')) is bool else None
        measured, reported_age = number(obj.get('measured_at')), number(obj.get('age_seconds'))
        actual_age = now - measured if measured is not None else None
        fresh = (obj.get('fresh') is True and actual_age is not None and 0 <= actual_age <= 10
                 and reported_age is not None and 0 <= reported_age <= 10)
        out['telemetry'] = {'source': key, 'fresh': fresh, 'measured_at': measured,
                            'age_seconds': actual_age, 'values': clean}
        break
    return out


# Exactly the tested sanitizer executes remotely; no repo imports/installation.
REMOTE = 'import json, math, sys, time, urllib.request\n' + inspect.getsource(sanitize) + r'''
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8080/api/status', timeout=3) as response:
        payload = response.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError('response too large')
    print(json.dumps(sanitize(json.loads(payload), time.time()), sort_keys=True, allow_nan=False))
except Exception as exc:
    print(json.dumps({'error': type(exc).__name__}))
    sys.exit(2)
'''


def assess(value):
    if not isinstance(value, dict) or 'error' in value:
        return [('Local status endpoint', 'FAIL', 'unavailable or invalid')]
    overall = value.get('doctor_overall')
    runtime_fresh = (value.get('acceptance') or {}).get('checkpoint_freshness') == 'fresh' if isinstance(value.get('acceptance'), dict) else False
    known = overall in ('ok', 'attention', 'degraded', 'action', 'unknown')
    checks = [('Doctor report', 'PASS' if known else 'FAIL', overall if known else 'missing')]
    checks.append(('Doctor health', 'FAIL' if overall in ('degraded', 'action') else
                   'UNKNOWN' if overall in (None, 'unknown') or not runtime_fresh else 'PASS',
                   overall if known else 'unavailable'))
    areas = value.get('areas') if isinstance(value.get('areas'), dict) else {}
    thermal = areas.get('thermal/power')
    checks.append(('Doctor thermal assessment', 'FAIL' if thermal in ('degraded', 'action') else
                   'PASS' if thermal in ('ok', 'attention') and runtime_fresh else 'UNKNOWN',
                   thermal if thermal in ('ok', 'attention', 'degraded', 'action', 'unknown') else 'not reported'))
    telemetry = value.get('telemetry')
    if not isinstance(telemetry, dict):
        return checks + [('Telemetry integration', 'UNKNOWN', 'not exposed in supported status locations')]
    fresh = telemetry.get('fresh') is True
    checks.append(('Telemetry freshness', 'PASS' if fresh else 'FAIL', 'fresh' if fresh else 'stale or invalid timestamp'))
    values = telemetry.get('values') if isinstance(telemetry.get('values'), dict) else {}
    limits = {'cpu_temp_c': (-20, 150), 'cpu_load_pct': (0, 100), 'ram_pct': (0, 100), 'cpu_frequency_mhz': (1, 10000)}
    for key, (low, high) in limits.items():
        v = values.get(key)
        try:
            valid = type(v) in (int, float) and math.isfinite(v) and low <= v <= high
        except OverflowError:
            valid = False
        checks.append((key, 'PASS' if valid and fresh else 'UNKNOWN',
                       str(v) if valid and fresh else 'unavailable or stale'))
    return checks


def validate_target(host, key):
    ip = ipaddress.IPv4Address(host)
    if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        raise ValueError('expected a trusted private LAN IPv4 address')
    key = key.expanduser()
    if not key.is_file():
        raise ValueError('SSH key file not found')
    return ip, key


def fetch(ip, key, *, runner=subprocess.run):
    argv = ['ssh', '-T', '-i', str(key)]
    for setting in ('BatchMode=yes', 'StrictHostKeyChecking=yes', 'ConnectTimeout=8',
            'IdentitiesOnly=yes', 'PreferredAuthentications=publickey', 'PasswordAuthentication=no',
            'KbdInteractiveAuthentication=no', 'ClearAllForwardings=yes',
            'ForwardAgent=no', 'ForwardX11=no', 'PermitLocalCommand=no',
            'ControlMaster=no', 'ControlPath=none',
            'ServerAliveInterval=5', 'ServerAliveCountMax=2'):
        argv.extend(['-o', setting])
    argv.extend([f'pi@{ip}', 'python3 -B -c ' + shlex.quote(REMOTE)])
    try:
        result = runner(argv, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(type(exc).__name__) from exc
    if result.returncode:
        raise RuntimeError('remote read-only probe failed (SSH, Python, or endpoint)')
    try:
        if len(result.stdout) > 65536:
            raise ValueError
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or 'error' in value:
            raise ValueError
        return value
    except ValueError as exc:
        raise RuntimeError('invalid remote JSON') from exc


def main(argv=None, *, runner=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='192.168.12.115')
    parser.add_argument('--key', type=Path, default=Path.home() / '.ssh/redux_field_ed25519')
    args = parser.parse_args(argv)
    try:
        ip, key = validate_target(args.host, args.key)
        checks = assess(fetch(ip, key, runner=runner))
    except (RuntimeError, ValueError) as exc:
        print('Field status unavailable: ' + str(exc), file=sys.stderr)
        return 2
    print('REDUX FIELD STATUS — READ ONLY')
    for name, result, detail in checks:
        print(f'  {result:7} {name:29} {detail}')
    failures = sum(result == 'FAIL' for _, result, _ in checks)
    unknown = sum(result == 'UNKNOWN' for _, result, _ in checks)
    print(f'Results: {len(checks)-failures-unknown} PASS, {failures} FAIL, {unknown} UNKNOWN')
    return 1 if failures else 3 if unknown else 0


if __name__ == '__main__':
    raise SystemExit(main())
