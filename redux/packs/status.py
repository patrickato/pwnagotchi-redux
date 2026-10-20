"""Shared, read-only installed inventory with bounded pages and refresh cadence."""
from collections import OrderedDict
import threading
import time
from .manager import PackManager
from .locations import locations, mount_guard


def unavailable():
    return {'version': 1, 'plugins': [], 'runtime_activation': 'unknown',
            'availability': 'error', 'errors': [{'reason':
            'Plugin inventory unavailable; check captures mount, catalog and state; preserve corrupt state'}]}


class InventoryProvider:
    def __init__(self, root=None, state=None, clock=time.monotonic, guard_factory=mount_guard, runtime=None):
        self.runtime = runtime
        self.root, self.state = root, state
        self.guard_factory, self.clock = guard_factory, clock
        self.lock = threading.Lock()
        self.updated = None
        self.manager = None
        self.guard = lambda: None
        self.pages = OrderedDict()
        self.cached = {'version': 1, 'plugins': [], 'errors': [],
                       'runtime_activation': 'unknown', 'availability': 'unconfigured'}

    def __call__(self, offset=0):
        if type(offset) is not int or offset < 0:
            raise ValueError('invalid inventory offset')
        with self.lock:
            now = self.clock()
            if self.root and (self.updated is None or now - self.updated >= 10):
                self.pages.clear()
                try:
                    _, state = locations(self.root, self.state)
                    self.guard = self.guard_factory(state)
                    self.guard()
                    self.manager = PackManager(self.root, state)
                    self.guard()
                    self.cached = {'availability': 'inspected' if self.manager.root.is_dir() else 'unavailable',
                                   'measured_at': time.time()}
                except Exception:
                    self.manager = None
                    self.cached = unavailable()
                self.updated = now
            try:
                self.guard()
                if self.manager is not None and offset not in self.pages:
                    self.pages[offset] = self.manager.inventory(offset=offset)
                    if len(self.pages) > 4:
                        self.pages.popitem(last=False)
                page = self.pages.get(offset, {})
                result = {**page, **self.cached}
            except Exception:
                self.pages.clear()
                result = unavailable()
            if self.runtime is not None and self.manager is not None:
                from .control import digest
                evidence = self.runtime()
                result = merge_runtime(result, digest(self.manager._data), evidence)
            return {**result, 'plugins': result.get('plugins', []),
                    'offset': offset, 'sample_age_seconds': None if self.updated is None else max(0, now-self.updated),
                    'provenance': 'read-only manifest inspection; no plugin imports'}

    def summary(self):
        """Keep the live API below framebuffer limits; full schema is paginated."""
        page = self()
        return compact_inventory(page)


def configured_provider(runtime=None):
    return InventoryProvider(*locations(), runtime=runtime)


def compact_inventory(page):
    """Bound shared framebuffer data while retaining truthful pagination."""
    def bounded(value):
        return value[:96] if isinstance(value, str) else value
    rows = page.get('plugins', [])
    return {**page, 'plugins': [{key: bounded(row.get(key)) for key in
            ('name', 'version', 'state', 'requested_enabled', 'active', 'activation',
             'runtime', 'native', 'kind', 'manifest_sha256')}
            for row in rows[:12]], 'errors': page.get('errors', [])[:3],
            'next_offset': page.get('offset', 0) + 12 if len(rows) > 12 else page.get('next_offset')}


def merge_runtime(result, revision, evidence):
    """Pure merge: only a live owner supplier may provide activation evidence."""
    rows = []
    for installed in result.get('plugins', []):
        row = dict(installed)
        actual = evidence.get('plugins', {}).get(row['name'], {})
        matching = (actual.get('request_revision') == revision
                    and actual.get('manifest_sha256') == row.get('manifest_sha256'))
        native = row.get('native') or {}
        implementation_matches = (native.get('eligible') is True
                                  and actual.get('implementation') == native.get('implementation')
                                  and actual.get('source_sha256') == native.get('source_sha256'))
        if (matching and evidence.get('generation')
                and (actual.get('active') is not True or implementation_matches)):
            state = actual.get('state', 'unavailable')
            row.update(active=actual.get('active') is True and evidence.get('fresh') is True,
                       activation=state if evidence.get('fresh') or state == 'stopped' else 'degraded',
                       runtime=actual)
        elif actual:
            row.update(active=False, activation='unavailable')
        rows.append(row)
    return {**result, 'plugins': rows, 'runtime_generation': evidence.get('generation'),
            'runtime_fresh': evidence.get('fresh') is True,
            'runtime_activation': 'observed' if evidence.get('fresh') else 'unavailable',
            'runtime_observed_at': evidence.get('observed_at'),
            'runtime_age_seconds': evidence.get('age_seconds'),
            'runtime_error': evidence.get('error')}
