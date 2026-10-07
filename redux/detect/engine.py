"""Fan-in engine: one Frame → all registered detectors → list of Alerts.

Builds the detector set from the registry by default so every registered
detector runs. Optional explicit list / options for tests.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence

from redux.detect.alerts import Alert
from redux.detect.frames import Frame
from redux.detect.registry import REGISTRY, build_from_registry, list_detectors
from redux.detect.rogue_ap import TrustedNetwork


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
    ) -> None:
        if detectors is not None:
            self._detectors = list(detectors)
        else:
            self._detectors = build_from_registry(names=names, options=options)
            # Apply trusted list to any RogueAPDetector instances
            if trusted is not None:
                from redux.detect.rogue_ap import RogueAPDetector

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
