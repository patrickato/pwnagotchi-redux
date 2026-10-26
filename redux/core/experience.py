"""Experience layer — learn from what actually worked, honestly.

The Brain (`brain.py`) is deliberately **not** a channel-yield reinforcement
learner: we proved in sim that a learning policy does not reliably beat
greedy+random at channel selection, so we never sell "more handshakes." This
module keeps that honesty while giving the device a real memory of outcomes:

* It **records real outcomes** of capture attempts with their context
  (channel, band, hour-of-day, AP vendor) — never fabricated.
* It reports **observed statistics with sample sizes**, so every number is
  legible and its confidence is visible ("6/8 here" is not "75% trust me").
* Its ranking is **exploration-preserving** (a UCB bonus) so an under-sampled
  option is never starved — the opposite of a greedy policy that locks onto an
  early winner.

It is advisory and glass-box: it surfaces what happened and a reason, and the
Brain/Supervisor/operator decides. It fires nothing. Its honest job is
orchestration — *is this spot/hour worth staying in or returning to* — not a
claim to out-pick greedy on channels.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# Outcomes we count as a "success" (a real capture of some kind).
SUCCESS_OUTCOMES = frozenset({"handshake", "pmkid", "capture", "eapol", "recovered"})

# Dimensions we can slice experience by. Each maps to a context key.
DIMENSIONS = ("channel", "band", "hour", "vendor", "kind", "method")

_MAX_ROWS = 20000           # bound the log; prune oldest beyond this
_UCB_C = 1.2                # exploration weight (higher = explore more)
_PRIOR_A, _PRIOR_B = 1.0, 1.0   # Beta(1,1) uniform prior -> unseen == 0.5


@dataclass(frozen=True)
class Experience:
    ts: float
    outcome: str                 # raw outcome label (e.g. "handshake", "none")
    context: Dict[str, object]   # {channel, band, hour, vendor, kind, ...}

    @property
    def success(self) -> bool:
        return self.outcome in SUCCESS_OUTCOMES

    def to_dict(self) -> dict:
        return {"ts": self.ts, "outcome": self.outcome, "context": self.context}

    @classmethod
    def from_dict(cls, d: dict) -> "Experience":
        return cls(ts=float(d.get("ts", 0.0)), outcome=str(d.get("outcome", "none")),
                   context=dict(d.get("context", {})))


def _hour_of(ts: float) -> int:
    return int(time.localtime(ts).tm_hour)


@dataclass(frozen=True)
class Stat:
    value: object        # the dimension value (e.g. channel 6)
    successes: int
    trials: int
    rate: float          # raw observed rate (successes/trials), 0 if no trials
    smoothed: float      # Beta-smoothed rate (prior-aware)
    score: float         # UCB score used for ranking (exploration-aware)
    reason: str          # glass-box, includes the sample size

    def to_dict(self) -> dict:
        return asdict(self)


def aggregate(rows: Iterable[Experience], dimension: str) -> Dict[object, Tuple[int, int]]:
    """{value: (successes, trials)} for a dimension over the given rows."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r} (choose {DIMENSIONS})")
    out: Dict[object, List[int]] = {}
    for r in rows:
        v = r.context.get(dimension)
        if v is None:
            continue
        cell = out.setdefault(v, [0, 0])
        cell[1] += 1
        if r.success:
            cell[0] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def _confidence(trials: int) -> str:
    if trials == 0:
        return "untried"
    if trials < 3:
        return "very low confidence"
    if trials < 8:
        return "low confidence"
    if trials < 25:
        return "moderate confidence"
    return "high confidence"


def rank(rows: List[Experience], dimension: str, *,
         candidates: Optional[Iterable[object]] = None,
         ucb_c: float = _UCB_C) -> List[Stat]:
    """Rank candidate values of a dimension by an exploration-aware score.

    score = smoothed_rate + c * sqrt(ln(total_trials+1) / (trials+1))

    The UCB bonus means an under-sampled (or untried) candidate gets a lift, so
    it's never starved — the explicit opposite of greedy lock-in. Every Stat
    carries successes/trials and a plain-English confidence so the number is
    honest about how much it's based on.
    """
    agg = aggregate(rows, dimension)
    total = sum(n for _, n in agg.values())
    cand = list(candidates) if candidates is not None else list(agg.keys())
    stats: List[Stat] = []
    for v in cand:
        s, n = agg.get(v, (0, 0))
        raw = (s / n) if n else 0.0
        smoothed = (s + _PRIOR_A) / (n + _PRIOR_A + _PRIOR_B)
        bonus = ucb_c * math.sqrt(math.log(total + 1) / (n + 1))
        score = smoothed + bonus
        conf = _confidence(n)
        if n == 0:
            reason = f"{dimension}={v}: untried — prior {smoothed:.2f}, worth a look (explore)"
        else:
            reason = (f"{dimension}={v}: {s}/{n} captures ({raw*100:.0f}%), "
                      f"{conf}; score {score:.2f}")
        stats.append(Stat(value=v, successes=s, trials=n, rate=raw,
                          smoothed=round(smoothed, 4), score=round(score, 4), reason=reason))
    stats.sort(key=lambda x: x.score, reverse=True)
    return stats


class ExperienceStore:
    """Append-only JSONL log of outcomes with glass-box stats. Pure-ish: all the
    math is in module functions over loaded rows, so it tests without a file."""

    def __init__(self, path, *, now=time.time, max_rows: int = _MAX_ROWS):
        self.path = Path(path)
        self._now = now
        self._max = max_rows

    # --- write ------------------------------------------------------------- #

    def record(self, outcome: str, context: Optional[dict] = None, *,
               ts: Optional[float] = None) -> Experience:
        """Append one real outcome. `hour` is derived from ts if not supplied."""
        t = self._now() if ts is None else ts
        ctx = dict(context or {})
        ctx.setdefault("hour", _hour_of(t))
        exp = Experience(ts=t, outcome=str(outcome), context=ctx)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(exp.to_dict(), sort_keys=True) + "\n")
        self._maybe_prune()
        return exp

    def _maybe_prune(self) -> None:
        try:
            rows = self.load()
        except OSError:
            return
        if len(rows) <= self._max:
            return
        keep = rows[-self._max:]
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in keep:
                f.write(json.dumps(r.to_dict(), sort_keys=True) + "\n")
        os.replace(tmp, self.path)

    # --- read -------------------------------------------------------------- #

    def load(self) -> List[Experience]:
        out: List[Experience] = []
        try:
            text = self.path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Experience.from_dict(json.loads(line)))
            except (ValueError, TypeError):
                continue
        return out

    def stats(self, dimension: str) -> List[Stat]:
        """Observed stats for a dimension, ranked (exploration-aware)."""
        return rank(self.load(), dimension)

    def summary(self) -> dict:
        rows = self.load()
        succ = sum(1 for r in rows if r.success)
        by_outcome: Dict[str, int] = {}
        for r in rows:
            by_outcome[r.outcome] = by_outcome.get(r.outcome, 0) + 1
        return {
            "total": len(rows),
            "successes": succ,
            "overall_rate": round(succ / len(rows), 4) if rows else 0.0,
            "by_outcome": by_outcome,
            "dimensions": list(DIMENSIONS),
        }

    def reset(self) -> str:
        try:
            self.path.unlink()
            return "cleared the experience log"
        except FileNotFoundError:
            return "no experience log to clear"


class CaptureRecorder:
    """Turns live offense into honest experience rows, one outcome per attempt.

    The double-counting trap: logging only successes makes every rate 100%;
    logging an attempt AND a success double-counts. So we *correlate*: each
    offense attempt against a BSSID is held pending with its context; if a
    handshake/PMKID for that BSSID arrives within `max_age` seconds it resolves
    to a success, otherwise :meth:`flush_stale` resolves it to a miss. Every
    attempt therefore produces exactly one row — a real rate.

    Decoupled and testable: inject the store and a clock; drive it with
    ``note_attempt`` + bus events + ``flush_stale`` using a fake clock.
    """

    def __init__(self, store: ExperienceStore, *, now=time.time, max_age: float = 180.0):
        self.store = store
        self._now = now
        self._max_age = max_age
        self._pending: Dict[str, Tuple[float, dict]] = {}   # bssid -> (ts, context)

    @staticmethod
    def _norm(bssid: str) -> str:
        return (bssid or "").strip().lower()

    def note_attempt(self, bssid: str, context: Optional[dict] = None, *,
                     now: Optional[float] = None) -> None:
        """Record that offense fired at `bssid`. Held pending until it resolves."""
        b = self._norm(bssid)
        if not b:
            return
        t = self._now() if now is None else now
        ctx = dict(context or {})
        ctx.setdefault("bssid", b)
        self._pending[b] = (t, ctx)

    def note_capture(self, bssid: str, outcome: str = "handshake", *,
                     now: Optional[float] = None) -> None:
        """A verified capture for `bssid` — resolve its pending attempt (if any)
        to a success. A capture with no recorded attempt is still logged (we did
        capture it), just without a paired trial inflating a miss."""
        b = self._norm(bssid)
        t = self._now() if now is None else now
        pend = self._pending.pop(b, None)
        ctx = pend[1] if pend else {"bssid": b}
        self.store.record(outcome, ctx, ts=t)

    def observe(self, em) -> None:
        """Bus subscriber: resolve attempts on handshake/PMKID events."""
        p = getattr(em, "payload", {}) or {}
        ev = p.get("event")
        etype = getattr(ev, "type", "")
        if etype not in ("handshake", "pmkid"):
            return
        data = getattr(ev, "data", {}) or {}
        bssid = (data.get("bssid") or data.get("mac") or data.get("address") or "")
        self.note_capture(bssid, "handshake" if etype == "handshake" else "pmkid")

    def flush_stale(self, now: Optional[float] = None) -> int:
        """Resolve attempts older than max_age as misses. Returns how many."""
        t = self._now() if now is None else now
        stale = [b for b, (ts, _) in self._pending.items() if (t - ts) >= self._max_age]
        for b in stale:
            _, ctx = self._pending.pop(b)
            self.store.record("none", ctx, ts=t)
        return len(stale)
