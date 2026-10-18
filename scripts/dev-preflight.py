#!/usr/bin/env python3
"""Detect local development requirements; never install or invoke sudo."""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
INSTALL = "python3 -m venv .venv && .venv/bin/python -m pip install -e '.[dev,crypto]'"


def checks(*, finder=importlib.util.find_spec, which=shutil.which, runner=subprocess.run):
    result = [("Python >= 3.11", "PASS" if sys.version_info >= (3, 11) else "FAIL", sys.version.split()[0])]
    for module in ('pytest', 'dbus_next', 'PIL', 'cryptography'):
        available = finder(module) is not None
        if available:
            try:
                available = runner([sys.executable, '-c', 'import ' + module],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=5, check=False).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                available = False
        result.append((module, 'PASS' if available else 'FAIL',
                       'import available' if available else INSTALL))
    try:
        compiler = shlex.split(os.environ.get('CC', 'cc'))
    except ValueError:
        compiler = []
    for label, args in [('bash', ['bash', '--version']), ('shellcheck', ['shellcheck', '--version']),
                        ('host C compiler', compiler + ['--version'] if compiler else []),
                        ('ELF linker', ['ld', '--version']), ('Git', ['git', '--version'])]:
        okay = bool(args and which(args[0]))
        if okay:
            try:
                okay = runner(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=5, check=False).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                okay = False
        result.append((label, 'PASS' if okay else 'FAIL', 'executable works' if okay else
                       'Ubuntu packages: build-essential binutils shellcheck git python3-venv'))
    node = which('node')
    node_ok = False
    if node:
        try:
            node_ok = runner([node, '-e', 'require("vm")'], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, timeout=5, check=False).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    result.append(('browser JavaScript tests', 'PASS' if node_ok else 'FAIL' if node else 'UNKNOWN',
                   'Node.js works' if node_ok else 'install a working Node.js runtime for browser contracts'))
    return result


def main():
    result = checks()
    for name, state, why in result:
        print(f'{state:7} {name}: {why}')
    print('No installation performed. Use a local venv; install Ubuntu packages only with operator consent.')
    return 1 if any(state == 'FAIL' for _, state, _ in result) else 0


if __name__ == '__main__':
    raise SystemExit(main())
