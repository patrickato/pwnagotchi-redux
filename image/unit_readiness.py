"""Strict offline checks for the shipped first-boot units, never systemctl.

Called by the existing root payload gate. Unsupported drop-ins are rejected
rather than silently interpreting a different effective unit configuration.
"""
from pathlib import Path

UNIT_KEYS = {'After', 'Before', 'Wants', 'Requires', 'Requisite', 'BindsTo',
             'RequiresMountsFor', 'ConditionPathExists', 'StartLimitIntervalSec', 'StartLimitBurst'}
TIMER_KEYS = {'Unit', 'OnBootSec', 'OnUnitInactiveSec', 'AccuracySec', 'Persistent'}


def section(key):
    return 'Unit' if key in UNIT_KEYS else 'Timer' if key in TIMER_KEYS else 'Service'


def parse(raw):
    values, current = {}, None
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if line.startswith('[') and line.endswith(']'):
            current = line[1:-1]
            continue
        if not current or '=' not in line or line.endswith('\\'):
            raise ValueError('unsupported/malformed first-boot unit syntax')
        key, value = line.split('=', 1)
        item = (current, key)
        if item in values and key != 'Environment':
            raise ValueError('duplicate/reset first-boot unit directive')
        values.setdefault(item, []).append(value)
    return values


def verify(root, required, reader):
    root = Path(root)
    graph = {}
    for unit, directives in required.items():
        values = parse(reader(root, 'etc/systemd/system/' + unit))
        for directive in directives:
            key, value = directive.split('=', 1)
            if value not in values.get((section(key), key), []):
                raise ValueError('first-boot directive in wrong section or missing: ' + unit + ' ' + directive)
        for prefix in ('etc', 'run', 'usr/lib', 'lib'):
            for folder in (unit + '.d', 'service.d' if unit.endswith('.service') else 'timer.d'):
                drop = root / prefix / 'systemd/system' / folder
                if drop.is_symlink() or drop.exists() and (not drop.is_dir() or any(drop.iterdir())):
                    raise ValueError('unverified first-boot unit drop-in: ' + unit)
        if unit != 'redux-fbui.service':
            for key in ('Requires', 'Requisite', 'BindsTo', 'Wants', 'After'):
                for dependency in values.get(('Unit', key), [''])[0].split():
                    if dependency == 'redux-fbui.service' or dependency.endswith('.device'):
                        raise ValueError('headless boot depends on optional hardware')
        for key in ('After', 'Before'):
            dependencies = values.get(('Unit', key), [''])[0].split()
            if len(dependencies) > 64:
                raise ValueError('first-boot dependency budget exceeded')
            for dependency in dependencies:
                a, b = (dependency, unit) if key == 'After' else (unit, dependency)
                graph.setdefault(a, set()).add(b)
    active, done = set(), set()
    def visit(node):
        if node in active:
            raise ValueError('first-boot service ordering cycle')
        if node in done:
            return
        active.add(node)
        for child in graph.get(node, ()):
            visit(child)
        active.remove(node); done.add(node)
    for node in graph:
        visit(node)
    return {'units_checked': len(required), 'ordering': 'acyclic', 'headless': True}
