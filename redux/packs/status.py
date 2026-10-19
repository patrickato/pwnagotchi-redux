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
    def __init__(self, root=None, state=None, clock=time.monotonic, guard_factory=mount_guard):
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
            return {**result, 'plugins': result.get('plugins', []),
                    'offset': offset, 'sample_age_seconds': None if self.updated is None else max(0, now-self.updated),
                    'provenance': 'read-only manifest inspection; no plugin imports'}

    def summary(self):
        """Keep the live API below framebuffer limits; full schema is paginated."""
        page = self()
        def bounded(value):
            return value[:96] if isinstance(value, str) else value
        return {**page, 'plugins': [{key: bounded(row.get(key)) for key in
                ('name', 'version', 'state', 'requested_enabled', 'active', 'activation')}
                for row in page.get('plugins', [])[:12]], 'errors': page.get('errors', [])[:3]}


def configured_provider():
    return InventoryProvider(*locations())
