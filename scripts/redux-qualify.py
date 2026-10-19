#!/usr/bin/env python3
"""One WSL qualification command. Default offline; --field adds a sanitized SSH status probe."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_gate(label, argv, env, *, runner=subprocess.run, timeout=900):
    # Keep noisy test output on WSL only; bounded tail on failure. Never used
    # for remote output (that path has an independent strict allowlist).
    with tempfile.TemporaryFile(mode='w+b') as log:
        try:
            completed = runner(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                               timeout=timeout, check=False)
            code = completed.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            return label, 'FAIL', type(exc).__name__
        log.seek(0, os.SEEK_END)
        size = log.tell()
        log.seek(max(0, size - 4000))
        tail = log.read().decode('utf-8', errors='replace').strip()
    if code:
        print(f'{label} failure (exit {code}):\n{tail}', file=sys.stderr)
    summary = next((line for line in reversed(tail.splitlines()) if 'passed' in line or 'failed' in line), '')
    return label, 'PASS' if code == 0 else 'FAIL', f'exit {code}' + ('; ' + summary if summary else '')


def summarize(checks):
    for name, status, detail in checks:
        print(f'{status:7} {name}: {detail}')
    counts = {s: sum(state == s for _, state, _ in checks) for s in ('PASS', 'FAIL', 'UNKNOWN')}
    print('Results: ' + ', '.join(f'{v} {k}' for k, v in counts.items()))
    return 1 if counts['FAIL'] else 3 if counts['UNKNOWN'] else 0


def main(argv=None, *, runner=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--beta', action='store_true', help='derive beta acceptance matrix from the same executed suites; missing physical evidence exits 3')
    parser.add_argument('--beta-report', type=Path, help='write beta JSON to a new local file (requires --beta)')
    parser.add_argument('--field', action='store_true', help='add read-only status SSH; no sudo or full report')
    parser.add_argument('--host', default='192.168.12.115')
    parser.add_argument('--key', type=Path, default=Path.home() / '.ssh/redux_field_ed25519')
    parser.add_argument('--country', help='optional operator-confirmed physical country; never applied to the Pi')
    parser.add_argument('--preflight-only', action='store_true', help='detect tools without running regressions')
    args = parser.parse_args(argv)
    if args.beta_report and not args.beta:
        parser.error('--beta-report requires --beta')
    beta = None
    preflight = load('dev-preflight').checks()
    checks = list(preflight)
    if not args.preflight_only and not any(state == 'FAIL' for _, state, _ in preflight):
        with tempfile.TemporaryDirectory(prefix='redux-qualify-') as scratch:
            env = dict(os.environ, PYTHONPYCACHEPREFIX=str(Path(scratch) / 'pycache'),
                       PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
            # The existing image runner owns its contract list. Do not duplicate
            # that list here, or add a second implementation of test discovery.
            gates = [('Python suite', [sys.executable, '-m', 'pytest', '-q', '-o', 'addopts=']),
                     ('Image contracts', ['bash', 'image/ci.sh']),
                     ('Python compile', [sys.executable, '-m', 'compileall', '-q', 'redux', 'scripts', 'boot', 'image']),
                     ('Shell syntax', ['bash', '-c', 'while IFS= read -r -d "" f; do bash -n "$f" || exit; done < <(git ls-files -z "*.sh")']),
                     ('Diff whitespace', ['git', 'diff', '--check'])]
            junit = Path(scratch) / 'tests.xml'
            if args.beta:
                gates[0][1].append('--junitxml=' + str(junit))
            for label, command in gates:
                print('Running ' + label + '...', flush=True)
                checks.append(run_gate(label, command, env, runner=runner))
            if args.beta:
                # Reuse this run's JUnit results; no duplicate test discovery or execution.
                if str(ROOT) not in sys.path:
                    sys.path.insert(0, str(ROOT))
                from redux.core.beta_readiness import assess
                beta = assess(checks, junit)
    elif not args.preflight_only:
        checks.append(('Offline regressions', 'UNKNOWN', 'blocked by failed development preflight'))
    if args.preflight_only:
        checks.append(('Offline regressions', 'UNKNOWN', 'preflight-only requested; regressions not run'))
    field_error = False
    if args.field:
        field = load('field-status')
        acceptance = load('field-acceptance')
        try:
            host, key = field.validate_target(args.host, args.key)
            if args.country is not None and (not re.fullmatch(r'[A-Z]{2}', args.country) or args.country == 'ZZ'):
                raise ValueError('country must be an operator-confirmed two-letter domain')
            value = field.fetch(host, key, runner=runner)
            checks.extend(field.assess(value))
            checks.extend(acceptance.status_verdicts(value.get('acceptance'), args.country))
        except (ValueError, RuntimeError) as exc:
            field_error = True
            checks.append(('Field status transport', 'FAIL', str(exc)))
    else:
        print('Offline only: no SSH attempted. PASS does not qualify hardware.')
    if args.beta:
        # Source binding is metadata, not a release signature or hardware gate.
        try:
            revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, timeout=2).strip()
            dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, timeout=2))
        except (OSError, subprocess.SubprocessError):
            revision, dirty = None, None
        if beta is None:
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from redux.core.beta_readiness import assess
            beta = assess(checks, Path('/nonexistent/redux-test-evidence'))
        beta.update(source_revision=revision, worktree_dirty=dirty, assessed_at=time.time())
        checks.extend(('Beta ' + row['gate'], row['status'], row['detail']) for row in beta['matrix'])
        print('Beta assessment: ' + beta['assessment'] + '; production readiness is not established.')
        if args.beta_report:
            try:
                # Exclusive creation prevents overwriting another engineer's evidence.
                with args.beta_report.open('x', encoding='utf-8') as stream:
                    json.dump(beta, stream, sort_keys=True, indent=2)
                    stream.write('\n')
            except OSError as error:
                checks.append(('Beta report', 'FAIL', type(error).__name__))
    result = summarize(checks)
    return 2 if field_error else result


if __name__ == '__main__':
    raise SystemExit(main())
