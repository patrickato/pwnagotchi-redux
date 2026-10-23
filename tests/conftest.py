"""Keep all test/interpreter caches in a disposable directory owned by this run."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest


def run_field_script_unprivileged(script, timeout=5):
    """Run a field script as a non-root user and return the CompletedProcess.

    The field scripts guard with an EUID!=0 check that exits 2 before any
    probing or mutation. A test that simply runs the script asserts that
    non-root path — but CI runners (and root dev boxes) execute the test
    process itself as root, so a bare invocation would sail past the EUID
    guard and hit the next check instead. Force the non-root path: when the
    test is root, drop to an unprivileged user; otherwise run directly.

    The script content is piped to `bash` on stdin rather than passed as a
    path, so the dropped user never needs to traverse/read the checkout (a CI
    runner's work tree is often not readable by `nobody`). Root reads the file;
    the unprivileged shell only sees the bytes and its own low EUID.
    """
    source = Path(script).read_text()
    if os.geteuid() != 0:
        return subprocess.run(["bash", "-s"], input=source,
                              capture_output=True, text=True, timeout=timeout)
    setpriv = shutil.which("setpriv")
    if setpriv is not None:
        return subprocess.run(
            [setpriv, "--reuid=65534", "--regid=65534", "--clear-groups", "bash", "-s"],
            input=source, capture_output=True, text=True, timeout=timeout)
    for dropper in ("runuser", "su"):
        tool = shutil.which(dropper)
        if tool is not None:
            return subprocess.run([tool, "-s", "/bin/bash", "nobody", "-c", "exec bash -s"],
                                  input=source, capture_output=True, text=True, timeout=timeout)
    pytest.skip("no privilege-drop tool (setpriv/runuser/su) to exercise the non-root guard")


def pytest_configure(config):
    config._redux_cache = tempfile.TemporaryDirectory(prefix='redux-pytest-cache-')
    config._redux_old_cache = (sys.pycache_prefix, os.environ.get('PYTHONPYCACHEPREFIX'))
    sys.pycache_prefix = config._redux_cache.name
    os.environ['PYTHONPYCACHEPREFIX'] = config._redux_cache.name


def pytest_unconfigure(config):
    if not hasattr(config, '_redux_cache'):
        return
    sys.pycache_prefix, old_env = config._redux_old_cache
    if old_env is None:
        os.environ.pop('PYTHONPYCACHEPREFIX', None)
    else:
        os.environ['PYTHONPYCACHEPREFIX'] = old_env
    config._redux_cache.cleanup()
