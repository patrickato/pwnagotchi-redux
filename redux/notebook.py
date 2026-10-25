"""Lab notebook — a glass-box session journal for learning.

Augur is a tool for learning offensive security hands-on. This records what a
lab session actually did — targets touched, handshakes captured, cracks run,
signals catalogued, modes toggled — as a timestamped timeline, and renders a
clean Markdown report. The point is to carry the *knowledge* out of the lab:
look back at "what did I do and what happened" instead of scrolling logs.

Entries are appended by the operator (`redux notebook log …`) or by subsystems
that want to leave a breadcrumb. Real data only: it records what it's told, with
timestamps; it summarizes, it doesn't embellish. Pure/testable — a JSONL store
with an injectable clock, and a report renderer that's a pure function of the
entries.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

# recognized entry kinds (free-form is allowed; these get nicer grouping)
KINDS = ("recon", "capture", "crack", "signal", "mode", "note", "offense", "scan")


@dataclass(frozen=True)
class NotebookEntry:
    ts: float
    kind: str
    summary: str
    detail: dict

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NotebookEntry":
        return cls(ts=float(d.get("ts", 0.0)), kind=str(d.get("kind", "note")),
                   summary=str(d.get("summary", "")), detail=dict(d.get("detail", {})))


class LabNotebook:
    def __init__(self, path, *, now=time.time):
        self.path = Path(path)
        self._now = now

    def record(self, kind: str, summary: str, *, detail: Optional[dict] = None,
               ts: Optional[float] = None) -> NotebookEntry:
        e = NotebookEntry(ts=(self._now() if ts is None else ts),
                          kind=(kind or "note"), summary=summary or "",
                          detail=dict(detail or {}))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(e.to_dict(), sort_keys=True) + "\n")
        return e

    def entries(self) -> List[NotebookEntry]:
        out: List[NotebookEntry] = []
        try:
            text = self.path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(NotebookEntry.from_dict(json.loads(line)))
            except (ValueError, TypeError, KeyError):
                continue
        return out

    def clear(self) -> str:
        try:
            self.path.unlink()
            return "cleared the lab notebook"
        except FileNotFoundError:
            return "notebook was already empty"

    def summary(self) -> dict:
        es = self.entries()
        by_kind: Dict[str, int] = {}
        for e in es:
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        span = (es[-1].ts - es[0].ts) if len(es) >= 2 else 0.0
        return {"entries": len(es), "by_kind": by_kind, "span_s": span}

    def report(self, *, title: str = "Lab session") -> str:
        """Render the session as a Markdown report (pure function of entries)."""
        es = self.entries()
        s = self.summary()
        lines = [f"# {title}", ""]
        if not es:
            lines.append("_No activity recorded._")
            return "\n".join(lines)
        first = time.strftime("%Y-%m-%d %H:%M", time.localtime(es[0].ts))
        last = time.strftime("%H:%M", time.localtime(es[-1].ts))
        lines += [f"**{len(es)} entries** · {first}–{last} · "
                  + ", ".join(f"{k}:{v}" for k, v in sorted(s['by_kind'].items())), ""]
        lines.append("## Timeline")
        for e in es:
            t = time.strftime("%H:%M:%S", time.localtime(e.ts))
            lines.append(f"- `{t}` **{e.kind}** — {e.summary}")
        return "\n".join(lines)
