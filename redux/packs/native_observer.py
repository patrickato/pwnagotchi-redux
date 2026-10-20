"""Reviewed read-only consumer of the supervisor's existing resource observations.

No hardware access, imports of plugins, filesystem access or radio operations.
Only explicitly selected, non-sensitive measurement fields leave this consumer.
"""
import math


class ResourceSummary:
    def start(self, options):
        self.running = True
        self.observations = 0
        self.last = None

    def observe(self, observation):
        if not self.running:
            raise RuntimeError('consumer stopped')
        self.observations += 1
        values = observation.get('values', {})
        def number(key):
            value = values.get(key)
            return value if type(value) in (int, float) and math.isfinite(value) else None
        self.last = {'measured_at': observation.get('measured_at'),
                     'fresh': observation.get('fresh') is True,
                     'cpu_temp_c': number('cpu_temp_c'),
                     'cpu_load_pct': number('cpu_load_pct'), 'ram_pct': number('ram_pct')}
        return {'observations': self.observations, 'measurement': self.last,
                'provenance': 'existing ResourceMonitor via resource_observation signal'}

    def stop(self):
        self.running = False
