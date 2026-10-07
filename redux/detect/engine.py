"""Fan-in engine: one Frame → all registered detectors → list of Alerts.

Builds the detector set from the registry by default so every registered
detector runs. Optional explicit list / options / legacy kwargs for tests.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence

from redux.detect.alerts import Alert
from redux.detect.beacon_spam import BeaconSpamDetector
from redux.detect.deauth_flood import DeauthFloodDetector
from redux.detect.frames import Frame
from redux.detect.registry import REGISTRY, build_from_registry, list_detectors
from redux.detect.rogue_ap import RogueAPDetector, TrustedNetwork
from redux.detect.surveillance_sweep import SurveillanceSweepDetector


class _Detector(Protocol):
    def feed(self, frame: Frame) -> Optional[Alert]: ...

    def reset(self) -> None: ...


class DetectEngine:
    """Runs registered defensive detectors over each frame."""

    def __init__(
        self,
        *,
        detectors: Optional[Sequence[_Detector]] = None,
        names: Optional[List[str]] = None,
        options: Optional[Dict[str, Any]] = None,
        trusted: Optional[Iterable[TrustedNetwork]] = None,
        # Legacy kwargs (tests / older callers)
        deauth: Optional[DeauthFloodDetector] = None,
        rogue: Optional[RogueAPDetector] = None,
        beacon_spam: Optional[BeaconSpamDetector] = None,
        sweep: Optional[SurveillanceSweepDetector] = None,
    ) -> None:
        legacy = any(x is not None for x in (deauth, rogue, beacon_spam, sweep))
        if detectors is not None:
            self._detectors = list(detectors)
        elif legacy:
            self.deauth = deauth if deauth is not None else DeauthFloodDetector()
            self.rogue = rogue if rogue is not None else RogueAPDetector(trusted or ())
            self.beacon_spam = (
                beacon_spam if beacon_spam is not None else BeaconSpamDetector()
            )
            self.sweep = sweep if sweep is not None else SurveillanceSweepDetector()
            self._detectors = [self.deauth, self.rogue, self.beacon_spam, self.sweep]
        else:
            self._detectors = build_from_registry(names=names, options=options)
            if trusted is not None:
                for i, d in enumerate(self._detectors):
                    if isinstance(d, RogueAPDetector):
                        self._detectors[i] = RogueAPDetector(trusted)

    @property
    def detector_count(self) -> int:
        return len(self._detectors)

    def registered_names(self) -> List[str]:
        return list_detectors()

    def reset(self) -> None:
        for d in self._detectors:
            d.reset()

    def feed(self, frame: Frame) -> List[Alert]:
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
