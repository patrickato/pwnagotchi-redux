"""Boot-POST — a power-on self-test that *cannot* fake a green.

When the device powers up, the TFT shows a boot checklist: each line resolves
live from `pending` to a real result. The whole point of this module is the
honesty guarantee the sibling platform called **"running != working"**, applied
to the one screen where a user is most tempted to trust a reassuring splash:

  - A check turns PASS **only** because its probe actually returned a healthy
    result this run. There is no setter, no "mark green," no cached pass — the
    verdict is a *pure function* of the probe outputs produced during `run()`.
  - A probe that raises is a **FAIL**, never a silent skip. A probe that can't
    measure its thing (no collector yet, no hardware to ask) returns UNKNOWN —
    and an UNKNOWN on a *critical* check never reads as READY. "We couldn't
    check the radio" is not "the radio is fine."
  - So the only way the boot screen shows **READY** is if every critical probe
    genuinely passed. A blank/stub build can't boot to a confident green.

It reuses the Doctor's probes (same live capability graph / Governor / Scope),
so this is the Doctor rendered as a *gated, streaming boot sequence* rather than
a second, divergent health system. Pure logic, no I/O — the TFT, the web view,
and the boot log are all just renderers of the same results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterator, List, Optional, Sequence, Tuple, Union

from .doctor import (
    Doctor, DoctorInputs, Finding, Status,
    probe_capture_radio, probe_thermal_power, probe_location,
    probe_detectors, probe_scope,
)

# A probe returns (ok, reason): ok True→PASS, False→FAIL, None→UNKNOWN. A probe
# may instead return a PostStatus directly (the Doctor-derived checks do, so
# ATTENTION can surface as WARN). Either way, raising is always a FAIL.
ProbeRet = Tuple[Optional[Union[bool, "PostStatus"]], str]
Probe = Callable[[], ProbeRet]


class PostStatus(str, Enum):
    PENDING = "pending"     # not run yet (what the TFT shows before a line resolves)
    PASS = "pass"
    WARN = "warn"           # passed, but worth knowing (maps from Doctor ATTENTION)
    FAIL = "fail"
    UNKNOWN = "unknown"     # the probe had no basis to measure this


class Verdict(str, Enum):
    READY = "ready"         # every critical check passed this run
    DEGRADED = "degraded"   # boots, but a critical couldn't be confirmed or a non-critical failed
    HALT = "halt"           # a critical check failed — do not pretend to be fine


# ASCII glyphs — the target TFT is a tiny 480×320 panel, monochrome-safe.
_GLYPH = {PostStatus.PENDING: " ", PostStatus.PASS: "+", PostStatus.WARN: "~",
          PostStatus.FAIL: "X", PostStatus.UNKNOWN: "?"}

_PASSING = (PostStatus.PASS, PostStatus.WARN)   # "not failing" for the verdict


@dataclass(frozen=True)
class PostCheck:
    key: str
    label: str
    probe: Probe
    critical: bool = False


@dataclass(frozen=True)
class PostResult:
    key: str
    label: str
    status: PostStatus
    reason: str
    critical: bool
    index: int          # 1-based position in the sequence
    total: int


def _normalize(raw: Optional[Union[bool, PostStatus]]) -> PostStatus:
    """Map a probe's return into a status. The only path to PASS is an explicit
    healthy result; anything ambiguous is UNKNOWN, never PASS."""
    if isinstance(raw, PostStatus):
        return PostStatus.UNKNOWN if raw is PostStatus.PENDING else raw
    if raw is True:
        return PostStatus.PASS
    if raw is False:
        return PostStatus.FAIL
    return PostStatus.UNKNOWN   # None / anything else


# --- Doctor → boot mapping --------------------------------------------------- #

_FROM_DOCTOR = {
    Status.OK: PostStatus.PASS,
    Status.ATTENTION: PostStatus.WARN,
    Status.DEGRADED: PostStatus.FAIL,
    Status.ACTION_REQUIRED: PostStatus.FAIL,
    Status.UNKNOWN: PostStatus.UNKNOWN,
}


def _doctor_probe(fn: Callable[[DoctorInputs], Finding], inp: DoctorInputs) -> Probe:
    """Wrap a Doctor probe as a boot probe. Runs the probe *now* (at POST time),
    so the result reflects live state, not a value captured at construction."""
    def _run() -> ProbeRet:
        f = fn(inp)
        return _FROM_DOCTOR.get(f.status, PostStatus.UNKNOWN), (f.reason or f.summary)
    return _run


@dataclass
class PowerOnSelfTest:
    """An ordered checklist that resolves live and reports a derived verdict."""
    checks: List[PostCheck] = field(default_factory=list)

    # --- construction ------------------------------------------------------ #

    @classmethod
    def standard(cls, inp: DoctorInputs,
                 extra: Sequence[PostCheck] = ()) -> "PowerOnSelfTest":
        """The default boot sequence, derived from the live Doctor inputs.

        Only the capture radio is *critical*: without it the device cannot do
        its core job, so a boot that can't confirm it is not READY. Everything
        else is informative (location, scope, detectors) or self-healing
        (thermal/power, which the Governor manages), so it degrades at worst.
        Extra hardware probes (TFT init, RTC set, storage writable) can be
        appended by the boot layer that actually owns that hardware.
        """
        checks = [
            PostCheck("capture", "Capture radio", _doctor_probe(probe_capture_radio, inp), critical=True),
            PostCheck("thermal", "Thermal / power", _doctor_probe(probe_thermal_power, inp)),
            PostCheck("detectors", "Detectors armed", _doctor_probe(probe_detectors, inp)),
            PostCheck("location", "Location fix", _doctor_probe(probe_location, inp)),
            PostCheck("scope", "Authorized scope", _doctor_probe(probe_scope, inp)),
        ]
        checks.extend(extra)
        return cls(checks=checks)

    # --- run --------------------------------------------------------------- #

    def run(self) -> Iterator[PostResult]:
        """Execute each check in order, yielding its result as it resolves — so
        a renderer can paint `pending → pass/fail` line by line. A probe that
        raises becomes a FAIL carrying the error text (never a skipped green)."""
        total = len(self.checks)
        for i, c in enumerate(self.checks, start=1):
            try:
                raw, reason = c.probe()
                status = _normalize(raw)
            except Exception as e:  # a probe blowing up is a failure, not a pass
                status, reason = PostStatus.FAIL, f"probe error: {e}"
            yield PostResult(c.key, c.label, status, reason, c.critical, i, total)

    def results(self) -> List[PostResult]:
        return list(self.run())

    # --- verdict (pure function of results) -------------------------------- #

    @staticmethod
    def verdict(results: Sequence[PostResult]) -> Verdict:
        crit = [r for r in results if r.critical]
        if any(r.status is PostStatus.FAIL for r in crit):
            return Verdict.HALT
        # a critical we couldn't confirm, or any non-critical outright failure
        if any(r.status is PostStatus.UNKNOWN for r in crit) or \
           any(r.status is PostStatus.FAIL for r in results):
            return Verdict.DEGRADED
        return Verdict.READY

    @staticmethod
    def _verdict_reason(results: Sequence[PostResult], v: Verdict) -> str:
        if v is Verdict.HALT:
            bad = [r.label for r in results if r.critical and r.status is PostStatus.FAIL]
            return "critical check failed: " + ", ".join(bad)
        if v is Verdict.DEGRADED:
            unconf = [r.label for r in results if r.critical and r.status is PostStatus.UNKNOWN]
            failed = [r.label for r in results if not r.critical and r.status is PostStatus.FAIL]
            parts = []
            if unconf:
                parts.append("couldn't confirm " + ", ".join(unconf))
            if failed:
                parts.append("degraded: " + ", ".join(failed))
            return "; ".join(parts) or "booting with reduced function"
        warned = [r.label for r in results if r.status is PostStatus.WARN]
        return "all critical checks passed" + (f" (note: {', '.join(warned)})" if warned else "")

    def report(self) -> dict:
        """Run the whole sequence and return a glass-box result + derived verdict."""
        results = self.results()
        v = self.verdict(results)
        counts = {s.value: sum(1 for r in results if r.status is s) for s in PostStatus
                  if s is not PostStatus.PENDING}
        return {
            "verdict": v.value,
            "ready": v is Verdict.READY,
            "reason": self._verdict_reason(results, v),
            "counts": counts,
            "checks": [
                {"key": r.key, "label": r.label, "status": r.status.value,
                 "reason": r.reason, "critical": r.critical}
                for r in results
            ],
        }

    # --- renderers --------------------------------------------------------- #

    @staticmethod
    def tft_frame(results: Sequence[PostResult], width: int = 40,
                  pending_after: int = -1) -> List[str]:
        """Monochrome-safe TFT frame. `pending_after` lets a live renderer show
        the still-unresolved tail as `[ ]` while the sequence streams in; pass
        -1 (default) to render every result as resolved."""
        lines = ["redux POST"]
        for r in results:
            status = PostStatus.PENDING if (0 <= pending_after < r.index) else r.status
            mark = "*" if r.critical else " "
            body = f"[{_GLYPH[status]}]{mark}{r.label}"
            lines.append(body if len(body) <= width else body[: width - 1] + "…")
        if pending_after < 0 and results:
            v = PowerOnSelfTest.verdict(results)
            lines.append(f"-- {v.value.upper()} --")
        return lines

    @staticmethod
    def console(results: Sequence[PostResult]) -> str:
        """A boot-log rendering (one line per check + a verdict footer)."""
        out = []
        for r in results:
            crit = " (critical)" if r.critical else ""
            out.append(f"[{r.status.value.upper():7}] {r.label}{crit}"
                       + (f" — {r.reason}" if r.reason else ""))
        if results:
            v = PowerOnSelfTest.verdict(results)
            out.append(f"POST: {v.value.upper()} — {PowerOnSelfTest._verdict_reason(results, v)}")
        return "\n".join(out)
