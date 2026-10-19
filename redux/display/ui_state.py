"""Bounded read-only presentation contract shared by HTTP and native UI.

No I/O, cached values, device control or optimistic health inference. Rows carry
independent measurement freshness; a live process does not refresh its sensors.
"""
from __future__ import annotations

import math
import time

PAGES = ('overview', 'captures', 'doctor', 'radios', 'trends', 'system')


def mapping(value):
    return value if isinstance(value, dict) else {}


def text(value, limit=160):
    return ''.join(c if c.isprintable() else ' ' for c in value[:limit]) if isinstance(value, str) else 'Unavailable'


def number(value, low=0, high=1e18):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) and low <= value <= high else None
    except (OverflowError, ValueError):
        return None


def freshness(stamp, now, maximum):
    stamp, now = number(stamp), number(now)
    age = now - stamp if stamp is not None and now is not None else None
    return {'state': 'fresh' if age is not None and 0 <= age <= maximum else 'unknown' if age is None else 'stale',
            'measured_at': stamp, 'age_seconds': age, 'max_age_seconds': maximum}


def navigation(page, pages=PAGES, slots=5):
    """Sliding window: unrestricted page registry, fixed usable touch widths."""
    if page not in pages or len(set(pages)) != len(pages) or slots < 1:
        raise ValueError('invalid navigation')
    start = min(max(0, pages.index(page) - slots + 2), max(0, len(pages) - slots))
    return pages[start:start + slots]


def model(report, *, now=None):
    now = time.time() if now is None else now
    report = mapping(report)
    runtime, telemetry = mapping(report.get('runtime')), mapping(report.get('telemetry'))
    values, sources = mapping(telemetry.get('values')), mapping(telemetry.get('provenance'))
    heartbeat = freshness(runtime.get('updated_utc'), now, 20)
    sensors = freshness(telemetry.get('measured_at'), now, 10)
    age = number(telemetry.get('age_seconds'), 0, 10)
    if telemetry.get('fresh') is not True or age is None:
        sensors['state'] = 'stale' if sensors['measured_at'] is not None else 'unknown'
    visual = freshness(runtime.get('visual_sampled_utc'), now,
                       number(runtime.get('visual_max_age_seconds'), 1, 300) or 12)
    rows = []

    def row(label, value, state='unknown', source=None, measured_at=None):
        rows.append({'label': label, 'value': text(value), 'state': state,
                     'source': text(source), 'measured_at': number(measured_at)})

    specs = [('CPU temperature', 'cpu_temp_c', -20, 150, ' C'),
             ('CPU utilization', 'cpu_load_pct', 0, 100, '%'),
             ('Memory utilization', 'ram_pct', 0, 100, '%'),
             ('CPU frequency', 'cpu_frequency_mhz', 1, 10000, ' MHz'),
             ('Battery', 'battery_pct', 0, 100, '%')]
    for label, key, low, high, unit in specs:
        value = number(values.get(key), low, high)
        row(label, f'{value:.1f}{unit}' if value is not None else 'Unavailable',
            sensors['state'] if value is not None else 'unknown', sources.get(key), sensors['measured_at'])
    mask = values.get('throttled_mask')
    mask = mask if type(mask) is int and 0 <= mask <= 0xffffffff else None
    row('Firmware flags', f'0x{mask:x}' if mask is not None else 'Unavailable',
        sensors['state'] if mask is not None else 'unknown', sources.get('throttled_mask'), sensors['measured_at'])
    active = [label for bit, label in ((0, 'undervoltage'), (1, 'frequency capped'), (2, 'throttled'), (3, 'temperature limit')) if mask is not None and mask & (1 << bit)]
    row('Firmware current', ', '.join(active) if active else 'No active firmware flags' if mask is not None else 'Unavailable',
        sensors['state'] if mask is not None else 'unknown', sources.get('throttled_mask'), sensors['measured_at'])
    power = values.get('on_battery')
    row('Power source', 'Battery' if power is True else 'External' if power is False else 'Unavailable',
        sensors['state'] if type(power) is bool else 'unknown', sources.get('on_battery'), sensors['measured_at'])
    governor = mapping(report.get('governor'))
    valid_governor = sensors['state'] == 'fresh' and governor.get('measurement_fresh') is True and any(item['state'] == 'fresh' for item in rows)
    row('Governor', text(governor.get('mode')), 'fresh' if valid_governor else 'unknown', governor.get('reason'), sensors['measured_at'])
    doctor = mapping(report.get('doctor'))
    findings = doctor.get('findings')
    findings = findings[:64] if isinstance(findings, list) else []
    for finding in findings[:16]:
        finding = mapping(finding)
        status = finding.get('status')
        if status in ('unknown', 'attention', 'degraded', 'action'):
            row('Doctor ' + status, text(finding.get('summary')),
                'unknown' if status == 'unknown' else heartbeat['state'],
                text(finding.get('remediation')) + ' / ' + text(finding.get('reason')))
    coverage = mapping(doctor.get('coverage')).get('not_assessed')
    if isinstance(coverage, list):
        for area in coverage[:8]:
            row('Coverage unknown', text(area), source='Doctor coverage.not_assessed')
    if not findings:
        row('Doctor coverage', 'Unavailable', source='No findings supplied')
    free = number(runtime.get('free_bytes'))
    row('Capture storage', f'{free / 1048576:.0f} MiB free' if free is not None else 'Unavailable',
        heartbeat['state'] if free is not None else 'unknown', 'runtime free_bytes', runtime.get('updated_utc'))
    row('Capture path', 'Configured / existence unverified' if isinstance(runtime.get('capture_file'), str) and runtime['capture_file'].strip() else 'Unavailable',
        source='A configured path is not an artifact inventory')
    row('Runtime heartbeat', heartbeat['state'], heartbeat['state'], 'runtime.updated_utc', heartbeat['measured_at'])
    row('Visual observations', visual['state'], visual['state'], 'runtime.visual_sampled_utc', visual['measured_at'])
    from .radio_panel import model as radio_model
    from .capture_panel import model as capture_model
    radios = radio_model(report, now=now)
    captures = capture_model(report, now=now)
    for role in ('onboard', 'usb'):
        device = radios[role]
        row(role.upper() + ' radio', device['iface'] + ' / ' + device['mode'],
            'unknown' if device['state'] != 'OBSERVED' else 'fresh',
            device['driver'] + ' / RX UNVERIFIED')
    row('Dual-radio readiness', radios['readiness']['state'], source=' + '.join(radios['readiness']['blockers']))
    row('Processing ledger', captures['ledger'], source='Existing capture-processing report')
    row('Processing worker', captures['scan_state'], source='Worker scan timestamp; not runtime heartbeat')
    return {'version': 1, 'heartbeat': heartbeat, 'sensors': sensors, 'visual': visual, 'rows': rows[:80]}
