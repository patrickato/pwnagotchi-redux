"""Keep all test/interpreter caches in a disposable directory owned by this run."""
import os
import sys
import tempfile


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
