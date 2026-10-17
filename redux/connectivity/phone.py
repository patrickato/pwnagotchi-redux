"""Pure Bluetooth phone-management session controller (no BlueZ side effects).

A separate, reviewed platform adapter must translate returned intents into
BlueZ / NetworkManager actions. This module never configures radios, calls a
shell, binds sockets, stores secrets, makes the device discoverable by default
or connects to an untrusted phone.

All clocks are monotonic seconds. Public status contains no peer identifiers.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class PhonePhase(str, Enum):
    DISABLED = "disabled"
    IDLE = "idle"
    PAIRING = "pairing"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    BACKOFF = "backoff"


class PhoneIntent(str, Enum):
    ENABLE_ADAPTER = "enable_adapter"
    DISABLE_ADAPTER = "disable_adapter"
    START_DISCOVERABLE = "start_discoverable"
    STOP_DISCOVERABLE = "stop_discoverable"
    CONNECT_TRUSTED = "connect_trusted"
    DISCONNECT = "disconnect"


@dataclass(frozen=True)
class PhonePolicy:
    pairing_window_s: int = 90
    initial_backoff_s: int = 5
    maximum_backoff_s: int = 300
    connect_timeout_s: int = 30

    def __post_init__(self):
        if type(self.connect_timeout_s) is not int or not 1 <= self.connect_timeout_s <= 120:
            raise ValueError("invalid connection timeout")
        if (type(self.pairing_window_s) is not int
                or not 15 <= self.pairing_window_s <= 300):
            raise ValueError("pairing window must be 15-300 seconds")
        if (type(self.initial_backoff_s) is not int
                or not 1 <= self.initial_backoff_s <= 60):
            raise ValueError("invalid reconnection retry delay")
        if (type(self.maximum_backoff_s) is not int
                or not self.initial_backoff_s <= self.maximum_backoff_s <= 3600):
            raise ValueError("invalid maximum reconnection delay")


class PhoneSession:
    """Explicit owner-triggered pairing; reconnect only after trusted bonding."""

    def __init__(self, policy: PhonePolicy | None = None):
        self.policy = policy or PhonePolicy()
        self.phase = PhonePhase.DISABLED
        self._last_now = 0.0
        self._pairing_until = 0.0
        self._retry_at = 0.0
        self._failures = 0
        self._trusted = False
        self._connect_until = 0.0

    def _clock(self, now: float) -> float:
        # Reject oversized clocks *before* converting to float: integer
        # conversion can overflow, and a huge finite float plus the pairing
        # window could become infinity (never-expiring discoverability).
        if (type(now) not in (float, int)
                or not 0 <= now <= 1_000_000_000_000
                or now < self._last_now):
            raise ValueError("invalid/non-monotonic phone-management time")
        self._last_now = float(now)
        return self._last_now

    def enable(self, *, now: float) -> tuple[PhoneIntent, ...]:
        self._clock(now)
        if self.phase is PhonePhase.DISABLED:
            self.phase = PhonePhase.IDLE
            return (PhoneIntent.ENABLE_ADAPTER,)
        return ()

    def disable(self, *, now: float) -> tuple[PhoneIntent, ...]:
        self._clock(now)
        if self.phase is PhonePhase.DISABLED:
            return ()
        actions = []
        if self.phase is PhonePhase.PAIRING:
            actions.append(PhoneIntent.STOP_DISCOVERABLE)
        if self.phase in (PhonePhase.CONNECTED, PhonePhase.CONNECTING):
            actions.append(PhoneIntent.DISCONNECT)
        actions.append(PhoneIntent.DISABLE_ADAPTER)
        self.phase = PhonePhase.DISABLED
        self._trusted = False
        self._failures = 0
        self._retry_at = self._pairing_until = self._connect_until = 0.0
        return tuple(actions)

    def open_pairing(self, *, owner_authorized: bool, now: float) -> tuple[PhoneIntent, ...]:
        self._clock(now)
        if owner_authorized is not True:
            raise PermissionError("operator confirmation required for pairing")
        if self.phase is not PhonePhase.IDLE:
            raise RuntimeError("pairing requires an idle, explicitly enabled adapter")
        self.phase = PhonePhase.PAIRING
        self._pairing_until = self._last_now + self.policy.pairing_window_s
        return (PhoneIntent.START_DISCOVERABLE,)

    def pairing_result(self, *, authenticated: bool, trusted: bool,
                       now: float) -> tuple[PhoneIntent, ...]:
        self._clock(now)
        if self.phase is not PhonePhase.PAIRING:
            raise RuntimeError("no pairing attempt is active")
        if self._last_now >= self._pairing_until:
            self.phase = PhonePhase.IDLE
            self._trusted = False
            return (PhoneIntent.STOP_DISCOVERABLE,)
        self.phase = PhonePhase.IDLE
        self._trusted = authenticated is True and trusted is True
        return (PhoneIntent.STOP_DISCOVERABLE,)

    def admit_verified_bond(self, *, owner_authorized: bool, authenticated: bool,
                            trusted: bool, now: float) -> tuple[PhoneIntent, ...]:
        """Admit separately verified existing bond; never pair or grant trust."""
        self._clock(now)
        if (self.phase is not PhonePhase.IDLE or owner_authorized is not True
                or authenticated is not True or trusted is not True):
            raise PermissionError('explicit authenticated trusted bond evidence required')
        self._trusted = True
        return ()

    def link_connected(self, *, authenticated: bool, trusted: bool,
                       now: float) -> tuple[PhoneIntent, ...]:
        self._clock(now)
        if (self.phase not in (PhonePhase.IDLE, PhonePhase.CONNECTING)
                or (self.phase is PhonePhase.CONNECTING and self._last_now >= self._connect_until)
                or not self._trusted or authenticated is not True
                or trusted is not True):
            raise PermissionError("only an authenticated, bonded phone may connect")
        self.phase = PhonePhase.CONNECTED
        self._failures = 0
        self._retry_at = 0.0
        return ()

    def link_lost(self, *, now: float) -> tuple[PhoneIntent, ...]:
        self._clock(now)
        if self.phase is not PhonePhase.CONNECTED:
            return ()
        self._failures = 0
        self._schedule_retry()
        return ()

    def connect_failed(self, *, now: float) -> tuple[PhoneIntent, ...]:
        self._clock(now)
        if self.phase is not PhonePhase.CONNECTING:
            raise RuntimeError("no outstanding trusted reconnect")
        self._schedule_retry()
        return ()

    def _schedule_retry(self):
        if not self._trusted:
            self.phase = PhonePhase.IDLE
            return
        self._failures += 1
        delay = min(self.policy.maximum_backoff_s,
                    self.policy.initial_backoff_s * 2 ** min(self._failures - 1, 20))
        self._retry_at = self._last_now + delay
        self.phase = PhonePhase.BACKOFF

    def tick(self, *, now: float) -> tuple[PhoneIntent, ...]:
        self._clock(now)
        if self.phase is PhonePhase.PAIRING and self._last_now >= self._pairing_until:
            self.phase = PhonePhase.IDLE
            self._trusted = False
            return (PhoneIntent.STOP_DISCOVERABLE,)
        if self.phase is PhonePhase.CONNECTING and self._last_now >= self._connect_until:
            self._schedule_retry()
            return (PhoneIntent.DISCONNECT,)
        if self.phase is PhonePhase.BACKOFF and self._last_now >= self._retry_at:
            self.phase = PhonePhase.CONNECTING
            self._connect_until = self._last_now + self.policy.connect_timeout_s
            return (PhoneIntent.CONNECT_TRUSTED,)
        return ()

    def status(self, *, now: float) -> dict:
        self._clock(now)
        return {
            "phase": self.phase.value,
            "discoverable": (self.phase is PhonePhase.PAIRING
                             and self._last_now < self._pairing_until),
            "trusted_bond_verified": self._trusted,
            "pairing_seconds_left": (max(0, math.ceil(self._pairing_until - now))
                                     if self.phase is PhonePhase.PAIRING else 0),
            "retry_seconds_left": (max(0, math.ceil(self._retry_at - now))
                                   if self.phase is PhonePhase.BACKOFF else 0),
            "last_failure_count": self._failures,
        }
