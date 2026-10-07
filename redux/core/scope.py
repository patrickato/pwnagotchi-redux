"""Scope — the one central authorized-target list every firing-capable part of
redux consults before it transmits.

This is the single source of truth for "what am I allowed to point this at." Every
offensive function (deauth, assoc/PMKID elicitation, evil-portal, the network-layer
kill-chain) asks the *same* Scope, so authorization is managed in one place and
changes once, not per-module. Scope decides WHERE the toolkit is aimed; it never
limits what the toolkit can do to something in scope.

Design goals (make it effortless for the operator):
  - add / remove / clear single targets, or **bulk-load** a pasted list
  - **named jobs** so a per-engagement scope can be armed and cleared as a unit
  - **expiry** so authorization that lapses (a finished job) stops authorizing on its own
  - **lab mode** / arm-own-gear to pre-authorize your own kit in one move
  - BSSID, SSID, and CIDR/IP targets (covers RF-layer and the network-layer pivot)
  - a persistent JSON store, edited by the CLI or by hand, atomic-written

The one rule that survives: it starts empty, so the device never fires at something
the operator did not choose. Arming is a single gesture; aiming is not optional.
Every authorization decision returns a human-readable reason (glass-box).
"""
from __future__ import annotations

import ipaddress
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import List, Optional


def _norm_bssid(b: str) -> str:
    return (b or "").strip().lower()


@dataclass(frozen=True)
class ScopeEntry:
    """One authorized target. `kind` is bssid | ssid | cidr."""
    kind: str
    value: str
    label: str = ""
    job: str = ""
    added: float = 0.0
    expires: Optional[float] = None   # epoch seconds; None = never expires

    def active(self, now: float) -> bool:
        return self.expires is None or now < self.expires

    def matches(self, *, bssid=None, ssid=None, ip=None) -> bool:
        if self.kind == "bssid" and bssid is not None:
            return _norm_bssid(self.value) == _norm_bssid(bssid)
        if self.kind == "ssid" and ssid is not None:
            return self.value == ssid                      # SSIDs are case-sensitive
        if self.kind == "cidr" and ip is not None:
            try:
                return ipaddress.ip_address(str(ip)) in ipaddress.ip_network(self.value, strict=False)
            except ValueError:
                return False
        return False


def classify_target(token: str) -> str:
    """Guess the kind of a pasted token: bssid (MAC), cidr (IP/range), else ssid."""
    t = token.strip()
    mac = t.replace("-", ":")
    parts = mac.split(":")
    if len(parts) == 6 and all(len(p) == 2 and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts):
        return "bssid"
    try:
        ipaddress.ip_network(t, strict=False)
        return "cidr"
    except ValueError:
        return "ssid"


@dataclass
class Scope:
    """The central authorized-target list. Empty by default."""
    entries: List[ScopeEntry] = field(default_factory=list)
    path: Optional[str] = None

    # --- queries ----------------------------------------------------------- #

    def active_entries(self, now: Optional[float] = None, job: Optional[str] = None) -> List[ScopeEntry]:
        now = time.time() if now is None else now
        out = [e for e in self.entries if e.active(now)]
        if job is not None:
            out = [e for e in out if e.job == job]
        return out

    @property
    def empty(self) -> bool:
        """True if nothing is currently authorized (no active, non-expired entry).
        This is the firing gate's hard default — empty means refuse."""
        return len(self.active_entries()) == 0

    def authorize(self, *, bssid=None, ssid=None, ip=None,
                  now: Optional[float] = None, job: Optional[str] = None) -> tuple[bool, str]:
        """Glass-box decision: (permitted, human-readable reason)."""
        now = time.time() if now is None else now
        active = self.active_entries(now=now, job=job)
        if not active:
            where = f" for job '{job}'" if job else ""
            return False, f"refused: scope is empty{where} — arm a target first"
        for e in active:
            if e.matches(bssid=bssid, ssid=ssid, ip=ip):
                exp = "no expiry" if e.expires is None else f"expires {time.strftime('%Y-%m-%d', time.gmtime(e.expires))}"
                tag = f" (job '{e.job}')" if e.job else ""
                return True, f"authorized: {e.kind} {e.value}{tag}, {exp}"
        target = bssid or ssid or ip or "?"
        return False, f"refused: {target} is not in the active scope ({len(active)} authorized target(s))"

    def permits(self, *, bssid=None, ssid=None, ip=None,
                now: Optional[float] = None, job: Optional[str] = None) -> bool:
        """Boolean form — drop-in for the driver's firing gate."""
        return self.authorize(bssid=bssid, ssid=ssid, ip=ip, now=now, job=job)[0]

    # --- management -------------------------------------------------------- #

    def add(self, value: str, kind: Optional[str] = None, *, label: str = "", job: str = "",
            expires: Optional[float] = None, now: Optional[float] = None) -> ScopeEntry:
        now = time.time() if now is None else now
        kind = kind or classify_target(value)
        value = _norm_bssid(value) if kind == "bssid" else value.strip()
        entry = ScopeEntry(kind=kind, value=value, label=label, job=job, added=now, expires=expires)
        # de-dupe on (kind, value, job): replace rather than stack
        self.entries = [e for e in self.entries if not (e.kind == kind and e.value == value and e.job == job)]
        self.entries.append(entry)
        return entry

    def remove(self, value: str, *, job: Optional[str] = None) -> int:
        v = value.strip()
        vb = _norm_bssid(value)
        before = len(self.entries)
        self.entries = [
            e for e in self.entries
            if not ((e.value == v or e.value == vb) and (job is None or e.job == job))
        ]
        return before - len(self.entries)

    def clear(self, job: Optional[str] = None) -> int:
        """Clear the whole scope, or just one job."""
        before = len(self.entries)
        if job is None:
            self.entries = []
        else:
            self.entries = [e for e in self.entries if e.job != job]
        return before - len(self.entries)

    def jobs(self) -> List[str]:
        return sorted({e.job for e in self.entries if e.job})

    def bulk_load(self, text: str, *, job: str = "", label: str = "",
                  expires: Optional[float] = None, now: Optional[float] = None) -> int:
        """Load many targets from a pasted/importable list: one token per line,
        '#' comments and blank lines ignored, kind auto-detected per line. For the
        operator with a lot of gear or a big engagement scope — paste and go."""
        added = 0
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            self.add(line, label=label, job=job, expires=expires, now=now)
            added += 1
        return added

    def arm_lab(self, *, bssids=(), ssids=(), cidrs=(), label: str = "lab",
                now: Optional[float] = None) -> int:
        """Pre-authorize your own gear in one move (never expires). 'Lab mode'."""
        n = 0
        for b in bssids:
            self.add(b, "bssid", label=label, job="lab", now=now); n += 1
        for s in ssids:
            self.add(s, "ssid", label=label, job="lab", now=now); n += 1
        for c in cidrs:
            self.add(c, "cidr", label=label, job="lab", now=now); n += 1
        return n

    # --- persistence (atomic, infrequent — SD-friendly) -------------------- #

    def to_dict(self) -> dict:
        return {"version": 1, "entries": [asdict(e) for e in self.entries]}

    def save(self, path: Optional[str] = None) -> str:
        p = path or self.path
        if not p:
            raise ValueError("no path to save Scope to")
        tmp = f"{p}.tmp"
        with open(tmp, "w") as f:
            json.dump(self.to_dict(), f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)          # atomic rename
        self.path = p
        return p

    @classmethod
    def load(cls, path: str) -> "Scope":
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return cls(entries=[], path=path)   # missing/corrupt → empty scope, never a crash
        entries = []
        for d in data.get("entries", []):
            try:
                entries.append(ScopeEntry(
                    kind=d["kind"], value=d["value"], label=d.get("label", ""),
                    job=d.get("job", ""), added=float(d.get("added", 0.0)),
                    expires=(None if d.get("expires") is None else float(d["expires"])),
                ))
            except (KeyError, TypeError, ValueError):
                continue   # skip malformed rows, keep the rest
        return cls(entries=entries, path=path)

    def summary(self) -> dict:
        now = time.time()
        active = self.active_entries(now=now)
        return {
            "total": len(self.entries),
            "active": len(active),
            "expired": len(self.entries) - len(active),
            "jobs": self.jobs(),
            "empty": self.empty,
        }
