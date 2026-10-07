"""Fan-in engine: one Frame → all configured detectors → list of Alerts.

Keeps each detector independent (testable alone) while giving supervisors a
single call site. Still pure detection — no TX, no engine import.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Protocol

from redux.detect.alerts import Alert
from redux.detect.beacon_spam import BeaconSpamDetector
from redux.detect.deauth_flood import DeauthFloodDetector
from redux.detect.frames import Frame
from redux.detect.rogue_ap import RogueAPDetector, TrustedNetwork
from redux.detect.surveillance_sweep import SurveillanceSweepDetector


class _Detector(Protocol):
    def feed(self, frame: Frame) -> Optional[Alert]: ...

    def reset(self) -> None: ...


class DetectEngine:
    """Runs a fixed set of defensive detectors over each frame."""

    def __init__(
        self,
        *,
        deauth: Optional[DeauthFloodDetector] = None,
        rogue: Optional[RogueAPDetector] = None,
        beacon_spam: Optional[BeaconSpamDetector] = None,
        sweep: Optional[SurveillanceSweepDetector] = None,
        trusted: Optional[Iterable[TrustedNetwork]] = None,
    ) -> None:
        self.deauth = deauth if deauth is not None else DeauthFloodDetector()
        self.rogue = rogue if rogue is not None else RogueAPDetector(trusted or ())
        self.beacon_spam = beacon_spam if beacon_spam is not None else BeaconSpamDetector()
        self.sweep = sweep if sweep is not None else SurveillanceSweepDetector()
        self._detectors: List[_Detector] = [
            self.deauth,
            self.rogue,
            self.beacon_spam,
            self.sweep,
        ]

    def reset(self) -> None:
        for d in self._detectors:
            d.reset()

    def feed(self, frame: Frame) -> List[Alert]:
        """Ingest one frame; return zero or more glass-box alerts."""
        out: List[Alert] = []
        for d in self._detectors:
            a = d.feed(frame)
            if a is not None:
                out.append(a)
        return out

    def feed_many(self, frames: Iterable[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            out.extend(self.feed(f))
        return out
