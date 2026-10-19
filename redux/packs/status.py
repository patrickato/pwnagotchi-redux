"""Bounded-cadence read-only inventory supplier for web consumers."""
import os
import threading
import time
from .manager import PackManager


class InventoryProvider:
    def __init__(self, root=None, state=None, clock=time.monotonic):
        self.root = root
        self.state = state
        self.clock = clock
        self.lock = threading.Lock()
        self.updated = None
        self.cached = {'version': 1, 'plugins': [], 'errors': [], 'runtime_activation': 'unknown', 'availability': 'unconfigured'}

    def __call__(self, offset=0):
        with self.lock:
            now = self.clock()
            if self.root and (self.updated is None or now - self.updated >= 10):
                try:
                    manager = PackManager(self.root, self.state)
                    self.cached = manager.inventory(limit=manager.inventory(limit=0)["total"])
                    self.cached['measured_at'] = time.time()
                    self.cached['availability'] = 'inspected' if manager.root.is_dir() else 'unavailable'
                except (OSError, ValueError, TypeError, RecursionError):
                    self.cached = {'version': 1, 'plugins': [], 'errors': [{'reason': 'Inventory unavailable; inspect local state'}], 'availability': 'error'}
                self.updated = now
            records = self.cached.get('plugins', [])
            return {**self.cached, 'plugins': records[offset:offset + 50], 'offset': offset, 'next_offset': offset + 50 if offset + 50 < len(records) else None, 'sample_age_seconds': None if self.updated is None else max(0, now - self.updated), 'provenance': 'read-only manifest inspection; no plugin imports'}


def configured_provider():
    return InventoryProvider(os.environ.get('REDUX_PACKS_DIR'), os.environ.get('REDUX_PACKS_STATE'))
