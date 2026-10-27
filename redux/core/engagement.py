"""Engagement as a first-class object — the case file.

Scope says what you were allowed to touch, the authlog says what you actually
did, the loot vault holds what you recovered, the notebook holds what you
noted, and the report is the deliverable. Today those are five separate things;
an engagement is the durable object that ties them into one case file with a
lifecycle: you OPEN it (snapshotting the authorization you're operating under),
work under it, and CLOSE it (which pulls the authlog + loot + notebook together
and freezes the report). It's the organizing structure for the kill-chain's
output and the thing you review afterward to learn from.

Durable on /captures/engagements/<id>.json, so a case survives reboot and can be
reopened for review. Pure/deterministic over injected data + an injected clock;
the report is built by the existing redux.report.build_report (no duplication).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Sequence

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG.sub("-", (name or "engagement").lower()).strip("-") or "engagement"


@dataclass
class Engagement:
    id: str
    name: str
    operator: str
    authorization: str              # the note of what authorizes this (contract, lab, CTF…)
    opened_at: float
    status: str = "active"          # active | closed
    closed_at: Optional[float] = None
    scope_opened: list = field(default_factory=list)   # armed targets at open (glass-box)
    counts: dict = field(default_factory=dict)
    report: Optional[dict] = None   # frozen at close

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Engagement":
        return cls(**{k: d.get(k) for k in (
            "id", "name", "operator", "authorization", "opened_at", "status",
            "closed_at", "scope_opened", "counts", "report")})


def _armed(scope, now: float) -> list:
    out = []
    if scope is not None and hasattr(scope, "active_entries"):
        try:
            for e in scope.active_entries(now=now):
                out.append({"kind": e.kind, "value": e.value, "job": e.job, "expires": e.expires})
        except Exception:
            pass
    return out


@dataclass
class EngagementStore:
    """Durable open/close lifecycle for engagement case files."""
    dir: str = "/captures/engagements"

    def _path(self, eid: str) -> str:
        return os.path.join(self.dir, f"{eid}.json")

    def _save(self, eng: Engagement) -> None:
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self._path(eng.id) + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(eng.to_dict(), fh, indent=2)
            os.replace(tmp, self._path(eng.id))
        except OSError:
            pass

    def get(self, eid: str) -> Optional[Engagement]:
        try:
            with open(self._path(eid)) as fh:
                return Engagement.from_dict(json.load(fh))
        except (OSError, ValueError):
            return None

    def list(self) -> List[Engagement]:
        out = []
        try:
            for f in sorted(os.listdir(self.dir)):
                if f.endswith(".json"):
                    e = self.get(f[:-5])
                    if e:
                        out.append(e)
        except OSError:
            pass
        out.sort(key=lambda e: e.opened_at, reverse=True)
        return out

    def active(self) -> Optional[Engagement]:
        for e in self.list():
            if e.status == "active":
                return e
        return None

    def open(self, name: str, *, operator: str = "operator", authorization: str = "",
             scope=None, now: Optional[float] = None, force: bool = False) -> Engagement:
        """Open a case file, snapshotting the authorization you're operating under.
        One active engagement at a time unless force=True."""
        now = time.time() if now is None else now
        existing = self.active()
        if existing is not None and not force:
            raise ValueError(f"an engagement is already open: {existing.id} "
                             f"({existing.name}) — close it first or pass force")
        eid = f"{_slug(name)}-{int(now)}"
        eng = Engagement(id=eid, name=name, operator=operator,
                         authorization=authorization or "(unspecified — lab/own gear)",
                         opened_at=now, scope_opened=_armed(scope, now))
        self._save(eng)
        return eng

    def close(self, eid: str, *, scope=None, authlog_entries: Sequence = (),
              loot: Optional[dict] = None, notebook_entries: Sequence = (),
              authlog_integrity: Optional[dict] = None,
              now: Optional[float] = None) -> Optional[Engagement]:
        """Close the case: pull authlog → actions, fold in loot + notebook counts,
        freeze the report (via the existing report builder), persist."""
        now = time.time() if now is None else now
        eng = self.get(eid)
        if eng is None or eng.status == "closed":
            return eng
        from ..report import build_report, actions_from_authlog
        actions = actions_from_authlog(list(authlog_entries)) if authlog_entries else []
        report = build_report(eng.name, eng.operator, scope, actions,
                              window=(eng.opened_at, now),
                              authlog_integrity=authlog_integrity, now=now)
        loot = loot or {}
        eng.counts = {
            "actions": len(actions),
            "unauthorized": report.get("unauthorized_count", 0),
            "loot_credentials": int(loot.get("credentials", 0)),
            "loot_files": int(loot.get("files", 0)),
            "notebook": len(list(notebook_entries)),
            "techniques": len(report.get("techniques_exercised", [])),
        }
        eng.report = report
        eng.status = "closed"
        eng.closed_at = now
        self._save(eng)
        return eng

    def summary(self, eid: str) -> dict:
        """A one-glance case summary for the status model / UIs."""
        eng = self.get(eid)
        if eng is None:
            return {"found": False}
        return {
            "found": True, "id": eng.id, "name": eng.name, "status": eng.status,
            "operator": eng.operator, "authorization": eng.authorization,
            "opened_at": eng.opened_at, "closed_at": eng.closed_at,
            "armed_at_open": len(eng.scope_opened), "counts": eng.counts,
            "integrity": (eng.report or {}).get("integrity") if eng.report else None,
        }
