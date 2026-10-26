"""Credential-reuse graph — one recovered password's blast radius.

`loot pivot` sprays recovered credentials at a subnet and banks what sticks. This
is the intelligence over that: the reuse MAP. Real compromises escalate because
people reuse passwords, so the question after a crack isn't "did it work here?"
but "everywhere this credential ALSO works." Given recovered credentials and the
hosts/services discovered, this plans the reuse attempts (origin-aware, so a cred
isn't re-tried where it came from) and, from the results, builds the graph:

  - per credential : its blast radius — every host/service it unlocks
  - per host       : its exposure — every credential that opens it
  - the headline   : the most valuable credential and the most exposed host

A + B ⇒ C: A (the loot vault) + B (the discovered hosts) ⇒ C (the lateral-movement
picture — where one password takes you). Scope-gated firing path; the planner and
graph are pure, the spray runs through an injected tester, so it's verified with
no network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# usernames/services worth trying first — an admin cred that reuses is the prize.
_PRIORITY_USERS = ("root", "administrator", "admin", "sa", "oracle")


@dataclass(frozen=True)
class CredPair:
    username: str
    secret: str
    origin: str = ""          # host/target it was recovered on (skip re-trying there)

    @property
    def key(self) -> str:
        return f"{self.username}:{self.secret}"


@dataclass(frozen=True)
class Target:
    host: str
    port: int = 0
    service: str = ""


@dataclass(frozen=True)
class ReuseAttempt:
    cred: CredPair
    target: Target


@dataclass(frozen=True)
class ReuseResult:
    cred: CredPair
    target: Target
    worked: bool
    reason: str = ""


def creds_from_vault(entries: Sequence) -> List[CredPair]:
    """Pull service credentials (username+secret) out of loot-vault entries."""
    out: List[CredPair] = []
    seen = set()
    for e in entries or ():
        user = getattr(e, "username", "") or (e.get("username") if isinstance(e, dict) else "")
        secret = getattr(e, "secret", "") or (e.get("secret") if isinstance(e, dict) else "")
        origin = getattr(e, "target", "") or (e.get("target") if isinstance(e, dict) else "")
        if user and secret and (user, secret) not in seen:
            seen.add((user, secret))
            out.append(CredPair(username=user, secret=secret, origin=origin))
    return out


def _priority(attempt: ReuseAttempt) -> tuple:
    u = attempt.cred.username.lower()
    rank = _PRIORITY_USERS.index(u) if u in _PRIORITY_USERS else len(_PRIORITY_USERS)
    return (rank, attempt.target.host, attempt.target.port)


@dataclass
class CredReuse:
    """Plan reuse attempts and graph the results. Scope-gated when a scope is given."""
    scope: object = None

    def plan(self, creds: Sequence[CredPair], targets: Sequence[Target], *,
             skip_origin: bool = True, now: Optional[float] = None) -> List[ReuseAttempt]:
        """Every in-scope (cred, target) pair worth trying, origin-aware, prioritized."""
        attempts: List[ReuseAttempt] = []
        for t in targets or ():
            if self.scope is not None:
                try:
                    ok, _why = self.scope.authorize(ip=t.host, now=now)
                except Exception:
                    ok = False
                if not ok:
                    continue
            for c in creds or ():
                if skip_origin and c.origin and c.origin == t.host:
                    continue          # it already worked here — that's not reuse
                attempts.append(ReuseAttempt(cred=c, target=t))
        attempts.sort(key=_priority)
        return attempts

    def spray(self, creds: Sequence[CredPair], targets: Sequence[Target], *,
              tester: Callable[[str, int, str, str, str], bool],
              authorized: bool = False, lab: bool = False,
              now: Optional[float] = None) -> List[ReuseResult]:
        """Run the planned attempts through an injected tester. Firing path: FIELD
        needs --authorized (+ scope); LAB runs unleashed. tester(host,port,service,
        user,secret) -> bool."""
        if not lab and not authorized:
            return []
        results: List[ReuseResult] = []
        for a in self.plan(creds, targets, now=now):
            try:
                ok = bool(tester(a.target.host, a.target.port, a.target.service,
                                 a.cred.username, a.cred.secret))
            except Exception as e:
                results.append(ReuseResult(a.cred, a.target, False, f"error: {type(e).__name__}"))
                continue
            results.append(ReuseResult(a.cred, a.target, ok,
                                       "reused" if ok else "rejected"))
        return results

    def graph(self, results: Sequence[ReuseResult]) -> dict:
        """The reuse map: per-credential blast radius + per-host exposure + headline."""
        by_cred: Dict[str, List[str]] = {}
        by_host: Dict[str, List[str]] = {}
        for r in results or ():
            if not r.worked:
                continue
            tgt = r.target.host + (f":{r.target.port}" if r.target.port else "")
            by_cred.setdefault(r.cred.key, [])
            if tgt not in by_cred[r.cred.key]:
                by_cred[r.cred.key].append(tgt)
            by_host.setdefault(r.target.host, [])
            if r.cred.username not in by_host[r.target.host]:
                by_host[r.target.host].append(r.cred.username)
        top_cred = max(by_cred.items(), key=lambda kv: len(kv[1]), default=(None, []))
        top_host = max(by_host.items(), key=lambda kv: len(kv[1]), default=(None, []))
        return {
            "credentials_that_reuse": len(by_cred),
            "hosts_unlocked": len(by_host),
            "by_credential": {k: sorted(v) for k, v in by_cred.items()},
            "by_host": {k: sorted(v) for k, v in by_host.items()},
            "most_valuable_credential": ({"credential": top_cred[0].split(":")[0] + ":***",
                                          "unlocks": sorted(top_cred[1])} if top_cred[0] else None),
            "most_exposed_host": ({"host": top_host[0], "credentials": len(top_host[1])}
                                  if top_host[0] else None),
            "note": (f"{len(by_cred)} credential(s) reuse across {len(by_host)} host(s) — "
                     f"lateral movement confirmed" if by_cred
                     else "no credential reuse found (good hygiene, or nothing tried yet)"),
        }
