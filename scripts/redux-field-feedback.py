#!/usr/bin/env python3
"""Offline projection of the newest private field-bridge snapshot; no SSH.

Reads only a saved report already on the host. Stdout contains bounded,
whitelisted categories; no raw device file, key, host or wireless identifiers.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATE = Path.home() / '.local' / 'state' / 'redux-field-bridge'
MAX_REPORT = 128 * 1024
PATTERN = re.compile(r'session-[a-f0-9]{32}\.json\Z')


def private_report(source):
    source = Path(source).absolute()
    info = source.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid() or not 0 < info.st_size <= MAX_REPORT):
        raise ValueError('unsafe_saved_evidence')
    return source


def newest_private(state=None):
    base = STATE if state is None else Path(state)
    info = base.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.getuid()):
        raise ValueError('unsafe_evidence_directory')
    latest = None
    for entry in base.iterdir():
        if not PATTERN.fullmatch(entry.name):
            continue
        try:
            path = private_report(entry)
            meta = path.lstat()
        except (OSError, ValueError):
            continue
        item = (meta.st_mtime_ns, entry.name, path)
        if latest is None or item[:2] > latest[:2]:
            latest = item
    if latest is None:
        raise ValueError('no_private_reports')
    return latest[2]


def collect_feedback(report, *, runner=subprocess.run):
    from redux.field_feedback import facts
    from redux.field_feedback_contract import validate_and_encode_feedback
    report = private_report(report)
    argv = [sys.executable, str(ROOT / 'scripts' / 'redux-acceptance.py'),
            '--json', 'session-inspect', '--session-file', str(report)]
    try:
        proc = runner(argv, cwd=ROOT, capture_output=True, text=True,
                      check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('offline_inspection_unavailable') from exc
    if proc.returncode != 3 or not isinstance(proc.stdout, str) or len(proc.stdout) > 128*1024:
        raise ValueError('offline_inspection_failed')
    try:
        projected = json.loads(proc.stdout)
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid_offline_inspection') from exc
    # Enforce the same complete nested allowlist before writing even to
    # stdout, not only when Codex later consumes the result.
    return validate_and_encode_feedback(facts(projected))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    args=parser.add_mutually_exclusive_group(required=True)
    args.add_argument('--latest-private', action='store_true',
                      help='Use newest owner-private bridge report (offline only)')
    args.add_argument('--session-file', type=Path,
                      help='Read one existing owner-private report (offline only)')
    parsed=parser.parse_args(argv)
    try:
        selected = newest_private() if parsed.latest_private else private_report(parsed.session_file)
        print(collect_feedback(selected))
        return 0
    except (OSError, ValueError):
        print(json.dumps({'schema':1, 'format':'redux-field-feedback-v1',
                          'status':'unavailable','physical_acceptance':'UNKNOWN'},
                         sort_keys=True))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
