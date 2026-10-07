"""Read measured UPS state, publish it, and confirm low battery before poweroff."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import signal
import subprocess
from threading import Event

from redux.core.boot import atomic_checkpoint

DEFAULTS = {"enabled": False, "backend": "sysfs", "supply": "",
            "bus": 1, "gpio_chip": "", "ac_line": 6, "framebuffer": "",
            "threshold_percent": 10, "confirm_seconds": 30, "poll_seconds": 5}


def _opt(config, key):
    return config.get(key, DEFAULTS[key])


@dataclass(frozen=True)
class Reading:
    percent: float | None
    voltage_v: float | None
    status: str
    source: str
    reason: str


def measured(percent, voltage, status, source):
    if not math.isfinite(percent) or not 0 <= percent <= 100:
        raise ValueError("measured capacity outside 0..100 percent")
    if voltage is not None and (not math.isfinite(voltage) or voltage <= 0):
        raise ValueError("invalid measured voltage")
    if status not in {"Charging", "Discharging", "Full", "Not charging", "Unknown"}:
        raise ValueError("unrecognized supply status")
    return Reading(percent, voltage, status, source, "capacity read from the configured gauge; no voltage-to-percent estimate")


def read_sysfs(supply):
    supply = Path(supply)
    if (supply / "type").read_text().strip() not in {"Battery", "UPS"}:
        raise ValueError("configured supply is not a battery or UPS")
    if (supply / "present").exists() and (supply / "present").read_text().strip() != "1":
        raise ValueError("battery reports absent")
    voltage = supply / "voltage_now"
    return measured(float((supply / "capacity").read_text()),
                    float(voltage.read_text()) / 1_000_000 if voltage.exists() else None,
                    (supply / "status").read_text().strip(), str(supply))


def read_geekworm(bus, ac_online=None):
    # Manufacturer X120x/X728 protocol: SMBus words need swapping to gauge order.
    def word(register):
        raw = bus.read_word_data(0x36, register)
        return ((raw & 255) << 8) | (raw >> 8)
    voltage = word(2) * 1.25 / 1000 / 16
    percent = word(4) / 256
    status = "Unknown" if ac_online is None else ("Not charging" if ac_online else "Discharging")
    return measured(percent, voltage, status, "Geekworm gauge on configured I2C bus, address 0x36")


class LowBattery:
    def __init__(self, threshold=10, confirm_seconds=30, max_sample_gap=10):
        if not 0 < threshold < 100 or not math.isfinite(confirm_seconds) or confirm_seconds <= 0:
            raise ValueError("shutdown requires a threshold in (0,100) and a positive confirmation interval")
        if not math.isfinite(max_sample_gap) or max_sample_gap <= 0:
            raise ValueError("shutdown requires a finite positive maximum sample gap")
        self.threshold = threshold
        self.confirm_seconds = confirm_seconds
        self.max_sample_gap = max_sample_gap
        self.since = None
        self.last = None

    def decision(self, reading, now):
        if not math.isfinite(now):
            raise ValueError("invalid monotonic timestamp")
        interruption = None
        if self.last is not None:
            gap = now - self.last
            if gap < 0:
                interruption = "monotonic clock moved backwards; confirmation restarted"
            elif gap > self.max_sample_gap:
                interruption = (f"UPS sample gap {gap:.1f}s exceeds {self.max_sample_gap:g}s; "
                                "confirmation restarted")
            if interruption is not None:
                self.since = None
        self.last = now
        if reading.percent is None or reading.status != "Discharging":
            self.since = None
            return False, "shutdown withheld: discharging capacity is not confirmed"
        if not math.isfinite(reading.percent) or not 0 <= reading.percent <= 100:
            self.since = None
            return False, "shutdown withheld: invalid measured capacity"
        if reading.percent > self.threshold:
            self.since = None
            return False, f"capacity {reading.percent:.1f}% exceeds shutdown threshold {self.threshold}%"
        if self.since is None:
            self.since = now
        elapsed = now - self.since
        reason = (
            f"discharging capacity {reading.percent:.1f}% at/below {self.threshold}% "
            f"for {elapsed:.0f}s; confirmation requires {self.confirm_seconds}s")
        if interruption is not None:
            reason = f"shutdown withheld: {interruption}; {reason}"
        return elapsed >= self.confirm_seconds, reason


def sample(config):
    try:
        backend = _opt(config, "backend")
        if backend == "sysfs":
            return read_sysfs(_opt(config, "supply"))
        if backend == "geekworm":
            import smbus
            import gpiod
            chip_path = _opt(config, "gpio_chip")
            online = None
            if chip_path:
                with gpiod.Chip(chip_path) as chip:
                    line = chip.get_line(int(_opt(config, "ac_line")))
                    line.request(consumer="redux-ups", type=gpiod.LINE_REQ_DIR_IN)
                    try:
                        online = bool(line.get_value())
                    finally:
                        line.release()
            bus = smbus.SMBus(int(_opt(config, "bus")))
            try:
                return read_geekworm(bus, online)
            finally:
                bus.close()
        raise ValueError("unsupported UPS backend")
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        return Reading(None, None, "Unknown", str(_opt(config, "backend")),
                       f"UPS reading unavailable ({error}); shutdown inhibited")


def shutdown(reason, run=subprocess.run, sync=os.sync):
    # Persist the actual decision before requesting systemd's orderly shutdown.
    atomic_checkpoint(Path("/captures/redux/power-shutdown.json"),
                      {"reason": reason, "observed_utc": datetime.now(timezone.utc).isoformat()})
    sync()
    run(["systemctl", "poweroff", "--no-block"], check=True, timeout=15)


def main():
    import time
    from battery_tft import show
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = json.loads(Path("/etc/redux/power.json").read_text())
    if _opt(config, "enabled") is not True:
        logging.info("UPS monitor disabled: no operator-selected HAT profile")
        return
    poll = float(_opt(config, "poll_seconds"))
    if not math.isfinite(poll) or not 0.5 <= poll <= 10:
        raise ValueError("poll_seconds must be within 0.5..10")
    # Allow one delayed/missed polling cycle; longer silence is not observed discharge.
    policy = LowBattery(float(_opt(config, "threshold_percent")),
                        float(_opt(config, "confirm_seconds")), max_sample_gap=2 * poll)
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    last_reason = None
    while not stop.is_set():
        reading = sample(config)
        poweroff, reason = policy.decision(reading, time.monotonic())
        record = asdict(reading) | {"observed_utc": datetime.now(timezone.utc).isoformat(),
                                   "shutdown": poweroff, "shutdown_reason": reason}
        atomic_checkpoint(Path("/run/redux-power/battery.json"), record)
        Path("/run/redux-power/battery.json").chmod(0o644)
        try:
            if _opt(config, "framebuffer"):
                show(reading, _opt(config, "framebuffer"))
        except (OSError, ValueError) as error:
            logging.warning("TFT battery display unavailable: %s", error)
        if reason != last_reason:
            logging.info("%s; %s", reading.reason, reason)
            last_reason = reason
        if poweroff:
            shutdown(reason)
            return
        if stop.wait(poll):
            break


if __name__ == "__main__":
    main()
