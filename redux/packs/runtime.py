"""Opt-in, process-local owner for reviewed native plugins, never arbitrary imports.

Signal callbacks only replace one pending observation. Manifest/state IO and
trusted callbacks run on one daemon worker, away from the radio loop. Python
threads cannot forcibly interrupt a stuck callback: evidence expires and close
returns after a bounded join, reporting degradation rather than successful stop.
"""
from copy import deepcopy
import json
import math
import threading
import time
import uuid

from .control import digest, read_data
from .locations import locations, mount_guard
from .manager import PackManager
from .registry import resolve, REGISTRY
from ..core.signals import Signal
from ..core.telemetry import MAX_AGE_SECONDS


class NativeRuntime:
    def __init__(self, bus, root=None, state=None, *, clock=time.monotonic):
        self.bus = bus
        self.root, self.state = locations(root, state)
        self.clock = clock
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.halt = threading.Event()
        self.thread = None
        self.off = None
        self.pending = None
        self.rows = {}
        self.instances = {}
        self.dependencies = {}
        self.revision = None
        self.inventory = None
        self.request_revision = None
        self.inspected = None
        self.generation = uuid.uuid4().hex
        self.updated = None
        self.updated_utc = None
        self.busy_since = None
        self.error = None
        self.next_scan = float('-inf')
        self.guard = None

    def start(self):
        if self.halt.is_set():
            raise RuntimeError("closed owner; create a fresh process generation")
        if self.thread is not None:
            return
        self.off = self.bus.on(Signal.RESOURCE_OBSERVATION, self._enqueue)
        self.thread = threading.Thread(target=self._run, name='redux-native-plugins', daemon=True)
        try:
            self.thread.start()
        except Exception:
            self.off()
            self.off = None
            self.thread = None
            raise

    def _enqueue(self, emission):
        # Producer supplies a small canonical projection, not a full status tree.
        source = emission.payload.get('observation')
        if not isinstance(source, dict):
            source = {}
        values = source.get('values')
        values = values if isinstance(values, dict) else {}
        def finite(value):
            return value if type(value) in (int, float) and math.isfinite(value) else None
        observation = {'measured_at': finite(source.get('measured_at')),
                       'fresh': source.get('fresh') is True,
                       'values': {key: finite(values.get(key)) for key in
                                  ('cpu_temp_c', 'cpu_load_pct', 'ram_pct')}}
        with self.lock:
            self.pending = observation
        self.wake.set()

    def _row(self, name, **fields):
        with self.lock:
            self.rows[name] = {**self.rows.get(name, {}), **fields}

    def _stop_instances(self):
        for name, instance in reversed(list(self.instances.items())):
            self._row(name, state='stopping', active=False)
            try:
                instance.stop()
                self._row(name, state='stopped', active=False)
            except Exception as error:
                self._row(name, state='degraded', active=False, reason='stop failed: ' + type(error).__name__)
        self.instances.clear()
        self.dependencies.clear()

    def _reconcile(self):
        if self.guard is None:
            self.guard = mount_guard(self.state)
        self.guard()
        if not self.root.is_dir():
            raise ValueError("native catalog unavailable")
        manager = PackManager(self.root, self.state)
        self.guard()
        revision = digest(manager._data)
        from .status import compact_inventory
        inventory = compact_inventory(manager.inventory(limit=12))
        inventory.update(availability='inspected', measured_at=time.time(),
                         provenance='native worker read-only manifest inspection')
        with self.lock:
            self.inventory = inventory
            self.request_revision = revision
            self.inspected = self.clock()
        # Catalog identity participates so a removed/changed manifest stops code.
        identity = digest([revision, [vars(pack) for pack in manager.list()]])
        if identity == self.revision:
            for name in self.instances:
                resolve(manager.get(name))
            return
        self._stop_instances()
        self.revision = identity
        with self.lock:
            self.rows = {}
        if manager.partial or manager.errors:
            raise ValueError('catalog incomplete or invalid')
        names = manager.resolve_order(sorted(manager._enabled))
        if names:
            manager.preview('enable', names[0])  # existing full dependency/config validation
        for pack in manager.list():
            if pack.name in REGISTRY and pack.name not in names:
                self._row(pack.name, requested_enabled=False, request_revision=revision,
                          manifest_sha256=pack.manifest_sha256, active=False, state='stopped')
        for name in names:
            if self.halt.is_set():
                break
            pack = manager.get(name)
            self._row(name, requested_enabled=True, request_revision=revision, active=False,
                      state='unavailable', eligible=False)
            try:
                spec = resolve(pack)
                if not all(dep in self.instances for dep in pack.requires):
                    raise ValueError('dependency not active')
                self._row(name, state='starting', eligible=True,
                          implementation=spec.identity, source_sha256=spec.source_sha256,
                          manifest_sha256=pack.manifest_sha256)
                instance = spec.create()
                try:
                    began = self.clock()
                    instance.start({})  # registry v1 has no options or capabilities
                    if self.clock() - began > 1:
                        raise TimeoutError('startup callback exceeded budget')
                    self.guard()
                    try:
                        current = json.loads(read_data(self.state))
                    except FileNotFoundError:
                        current = {}
                    if digest(current) != revision or self.halt.is_set():
                        raise ValueError('request changed during activation')
                    self.guard()
                except Exception:
                    try:
                        instance.stop()
                    except Exception:
                        pass
                    raise
                self.instances[name] = instance
                self.dependencies[name] = pack.requires
                self._row(name, state='active', active=True, activated_at=time.time(), reason='start callback completed')
            except Exception as error:
                self._row(name, state='degraded' if self.rows[name].get('eligible') else 'unavailable',
                          active=False, reason='activation refused or failed: ' + type(error).__name__)

    def _run(self):
        try:
            while not self.halt.is_set():
                self.wake.wait(1)
                self.wake.clear()
                if self.halt.is_set():
                    break
                with self.lock:
                    observation, self.pending = self.pending, None
                    self.busy_since = self.clock()
                try:
                    if self.guard is not None:
                        self.guard()
                    if self.clock() >= self.next_scan:
                        self._reconcile()
                        self.error = None
                        self.next_scan = self.clock() + 10
                    if observation is not None:
                        for name, instance in list(self.instances.items()):
                            try:
                                if not all(dep in self.instances for dep in self.dependencies.get(name, ())):
                                    raise RuntimeError('dependency no longer active')
                                began = self.clock()
                                output = instance.observe(observation)
                                if self.clock() - began > 1:
                                    raise TimeoutError('observation callback exceeded budget')
                                # Enforce bounded serializable output before publication.
                                if len(json.dumps(output, allow_nan=False)) > 4096:
                                    raise ValueError('plugin output budget exceeded')
                                self._row(name, result=output, observed_monotonic=self.clock())
                            except Exception as error:
                                self._row(name, state='degraded', active=False,
                                          reason='observation failed: ' + type(error).__name__)
                                try:
                                    instance.stop()
                                except Exception:
                                    pass
                                del self.instances[name]
                except Exception as error:
                    self._stop_instances()
                    self.revision = None
                    self.error = 'native runtime unavailable: ' + type(error).__name__
                    self.next_scan = self.clock() + 10
                finally:
                    with self.lock:
                        self.updated = self.clock()
                        self.updated_utc = time.time()
                        self.busy_since = None
        finally:
            self._stop_instances()

    def snapshot(self):
        with self.lock:
            now = self.clock()
            fresh = self.updated is not None and 0 <= now - self.updated <= 5
            blocked = self.busy_since is not None and now - self.busy_since > 1
            rows = deepcopy(self.rows)
            for row in rows.values():
                result = row.get('result')
                if isinstance(result, dict) and isinstance(result.get('measurement'), dict):
                    measurement = result['measurement']
                    measured = measurement.get('measured_at')
                    if (type(measured) not in (int, float) or not 0 <= time.time()-measured <= MAX_AGE_SECONDS
                            or now - row.get('observed_monotonic', float('-inf')) > MAX_AGE_SECONDS):
                        measurement['fresh'] = False
                if row.get('active') and (not fresh or blocked or self.halt.is_set()):
                    row.update(active=False, state='degraded', reason='runtime evidence stale or stopping')
            return {'version': 1, 'generation': self.generation,
                    'fresh': fresh and not blocked and not self.halt.is_set(),
                    'observed_at': self.updated_utc,
                    'age_seconds': None if self.updated is None else max(0, now-self.updated),
                    'max_age_seconds': 5,
                    'plugins': rows, 'error': self.error}

    def summary(self):
        """No filesystem access on the supervisor/checkpoint thread."""
        from .status import merge_runtime, unavailable
        evidence = self.snapshot()
        with self.lock:
            inventory = deepcopy(self.inventory) if self.inventory is not None else unavailable()
            revision = self.request_revision
            inventory['sample_age_seconds'] = (None if self.inspected is None else
                                               max(0, self.clock()-self.inspected))
        return merge_runtime(inventory, revision, evidence)

    def close(self):
        if self.off is not None:
            self.off()
            self.off = None
        self.halt.set()
        self.wake.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=0.25)
