"""Bounded read-only Linux/Pi resource sampling. No background thread or writes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import re
import subprocess
import time

from .governor import Reading, valid_reading, finite_number

SAMPLE_SECONDS = 2.0
MAX_AGE_SECONDS = 10.0
FLAGS = {0: 'undervoltage', 1: 'frequency_capped', 2: 'throttled', 3: 'soft_temperature_limit',
         16: 'undervoltage_occurred', 17: 'frequency_capped_occurred',
         18: 'throttled_occurred', 19: 'soft_temperature_limit_occurred'}


def _read(path):
    try:
        with path.open('r', encoding='ascii') as stream:
            value = stream.read(16385)
        return value.strip() if len(value) <= 16384 else None
    except (OSError, UnicodeError):
        return None


def _number(value, low, high, scale=1):
    try:
        result = float(value) / scale
        return result if math.isfinite(result) and low <= result <= high else None
    except (ValueError, TypeError, OverflowError):
        return None


def _throttled():
    try:
        result = subprocess.run(['/usr/bin/vcgencmd', 'get_throttled'],
                                capture_output=True, text=True, timeout=0.5, check=False)
        if result.returncode == 0 and re.fullmatch(r'throttled=0x[0-9a-fA-F]{1,8}\s*', result.stdout):
            return int(result.stdout.strip().split('=')[1], 16)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        pass
    return None


@dataclass(frozen=True)
class Snapshot:
    measured_at: float
    monotonic_at: float
    reading: Reading
    cpu_frequency_mhz: float | None
    throttled_mask: int | None
    provenance: dict

    def status(self, now, monotonic_now):
        valid_times = all(finite_number(t) for t in
                          (now, self.measured_at, monotonic_now, self.monotonic_at))
        age = now - self.measured_at if valid_times else None
        elapsed = monotonic_now - self.monotonic_at if valid_times else None
        fresh = (valid_times and 0 <= age <= MAX_AGE_SECONDS
                 and 0 <= elapsed <= MAX_AGE_SECONDS)
        values = asdict(valid_reading(self.reading))
        # There is no battery/power-source sensor in this collector.
        if values['battery_pct'] is None and self.reading.on_battery is not True:
            values['on_battery'] = None
        values['cpu_frequency_mhz'] = self.cpu_frequency_mhz
        values['throttled_mask'] = self.throttled_mask
        return {'measured_at': self.measured_at if finite_number(self.measured_at) else None, 'age_seconds': max(age, elapsed) if valid_times else None,
                'fresh': fresh, 'max_age_seconds': MAX_AGE_SECONDS,
                'values': values, 'provenance': dict(self.provenance),
                'flags': {name: bool(self.throttled_mask & (1 << bit))
                          for bit, name in FLAGS.items()} if self.throttled_mask is not None else None}


class ResourceCollector:
    """One owner samples on pump; status readers only inspect its cached snapshot.

    CPU utilization needs two advancing /proc/stat observations. Guest counters
    are already included in user/nice; iowait counts as idle. Failed/reset samples
    discard the baseline. Sysfs mtime is not sensor freshness: reads happen now.
    """
    def __init__(self, root=Path('/'), *, clock=time.time, monotonic=time.monotonic,
                 throttle_reader=_throttled):
        self.root = Path(root)
        self.clock, self.monotonic = clock, monotonic
        self.throttle_reader = throttle_reader
        self.snapshot = None
        self._cpu = None

    def sample(self):
        now, mono = self.clock(), self.monotonic()
        if self.snapshot is not None and 0 <= mono - self.snapshot.monotonic_at < SAMPLE_SECONDS:
            return None
        sources = {}
        def read(name):
            return _read(self.root / name)
        temp = None
        # Do not mislabel arbitrary host thermal zones as the Pi CPU.
        model = read('proc/device-tree/model') or ''
        pi = model.startswith('Raspberry Pi 4') or model.startswith('Raspberry Pi 5')
        if pi and read('sys/class/thermal/thermal_zone0/type') == 'cpu-thermal':
            temp = _number(read('sys/class/thermal/thermal_zone0/temp'), -20, 150, 1000)
            if temp is not None:
                sources['cpu_temp_c'] = 'sysfs:thermal_zone0/cpu-thermal'
        load, current = None, None
        try:
            fields = (read('proc/stat') or '').splitlines()[0].split()
            ticks = [int(v) for v in fields[1:9]]
            if fields[0] == 'cpu' and len(ticks) == 8 and all(v >= 0 for v in ticks):
                current = ticks
                if self._cpu is not None:
                    delta = [a - b for a, b in zip(ticks, self._cpu)]
                    total = sum(delta)
                    if all(v >= 0 for v in delta) and total > 0:
                        load = 100 * (total - delta[3] - delta[4]) / total
                        sources['cpu_load_pct'] = 'procfs:stat/cpu delta'
        except (IndexError, ValueError):
            pass
        self._cpu = current
        ram = None
        try:
            mem = {}
            for line in (read('proc/meminfo') or '').splitlines():
                fields = line.split()
                if fields[0] in ('MemTotal:', 'MemAvailable:') and len(fields) == 3 and fields[2] == 'kB':
                    mem[fields[0]] = int(fields[1])
            total, available = mem['MemTotal:'], mem['MemAvailable:']
            if total > 0 and 0 <= available <= total:
                ram = 100 * (total - available) / total
                sources['ram_pct'] = 'procfs:meminfo/MemAvailable'
        except (KeyError, IndexError, ValueError):
            pass
        freq = _number(read('sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq'), 1, 10000, 1000)
        if freq is not None:
            sources['cpu_frequency_mhz'] = 'sysfs:cpufreq/policy0/scaling_cur_freq'
        try:
            mask = self.throttle_reader() if pi else None
        except (OSError, subprocess.SubprocessError):
            mask = None
        if type(mask) is not int or not 0 <= mask <= 0xffffffff:
            mask = None
        if mask is not None:
            sources['throttled_mask'] = 'firmware:vcgencmd get_throttled'
        self.snapshot = Snapshot(now, mono, Reading(cpu_temp_c=temp, cpu_load_pct=load, ram_pct=ram),
                                 freq, mask, sources)
        return self.snapshot

    def status(self):
        return self.snapshot.status(self.clock(), self.monotonic()) if self.snapshot else None
