"""Engagement report — the chain-of-authorization deliverable.

This is the feature that makes a serious person trust the box. It cashes in
everything redux already records — the central Scope (what you were authorized to
hit), the glass-box reason on every action, the ATT&CK/PTES tags, and optionally
the Dex findings and a purple Range coverage score — into one report a pro can
hand a client.

Its integrity rests on one behaviour: **an out-of-scope action is surfaced, never
hidden.** The report re-checks every recorded action against the Scope and flags
any that wasn't authorized as ⚠ UNAUTHORIZED, and the header's integrity verdict
goes FLAGGED. A report that would expose its own out-of-scope action is one you can
actually trust; one that quietly omits it is worthless. Real data only — it
aggregates what happened, it never invents an action or a result.

Optional `sanitize=True` runs identifiers through the same ghost pseudonymizer as
replay/ghost, so a report can be shared without leaking real targets.
"""
from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from ..core.scope import classify_target
from ..frameworks import REGISTRY


@dataclass(frozen=True)
class EngagementAction:
    """One thing the operator did, as recorded during the engagement."""
    ts: float
    action: str                 # a frameworks registry key (deauth, evil_twin, …) or free text
    target: str                 # bssid / ssid / ip / cidr
    reason: str = ""            # the glass-box 'why' carried at the time
    result: str = ""            # outcome, if any (e.g. 'handshake captured')


@dataclass(frozen=True)
class ReportLine:
    ts: float
    action: str
    target: str
    authorized: bool
    auth_reason: str
    attack_ids: List[str]
    phase: str
    reason: str
    result: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _authorize(scope, target: str):
    """(authorized, reason) for one target against the Scope — defense in depth."""
    if scope is None:
        return False, "no scope provided — cannot confirm authorization"
    kind = classify_target(target)
    if kind == "bssid":
        return scope.authorize(bssid=target)
    if kind == "ssid":
        return scope.authorize(ssid=target)
    # cidr or ip → test an address for membership in an armed CIDR
    try:
        net = ipaddress.ip_network(target, strict=False)
        ip = str(net.network_address)
    except ValueError:
        ip = target
    return scope.authorize(ip=ip)


def _attack_for(action: str):
    m = REGISTRY.get(action.strip().lower()) if action else None
    if m is None:
        return [], "(unmapped)"
    return list(m.attack_ids), m.ptes_phase


def build_report(engagement: str, operator: str, scope,
                 actions: Sequence[EngagementAction], *,
                 dex_summary: Optional[dict] = None,
                 range_report: Optional[dict] = None,
                 window: Optional[tuple] = None,
                 sanitize: bool = False,
                 now: Optional[float] = None) -> dict:
    """Assemble the report as a structured dict (render with render_markdown)."""
    now = time.time() if now is None else now

    mapper = None
    if sanitize:
        from ..replay.ghost import GhostMapper
        mapper = GhostMapper(seed=f"report:{engagement}")

    def _disp(target: str) -> str:
        if not sanitize or mapper is None:
            return target
        kind = classify_target(target)
        if kind == "bssid":
            return mapper.mac(target)
        if kind == "ssid":
            return mapper.name(target, kind="net")
        return target     # CIDRs/IPs left as-is (networks aren't personally identifying)

    lines: List[ReportLine] = []
    for a in sorted(actions, key=lambda x: x.ts):
        authorized, auth_reason = _authorize(scope, a.target)
        attack_ids, phase = _attack_for(a.action)
        lines.append(ReportLine(
            ts=a.ts, action=a.action, target=_disp(a.target),
            authorized=authorized, auth_reason=auth_reason,
            attack_ids=attack_ids, phase=phase, reason=a.reason, result=a.result))

    unauthorized = [l for l in lines if not l.authorized]
    scope_summary = scope.summary() if scope is not None and hasattr(scope, "summary") else {}
    armed = []
    if scope is not None and hasattr(scope, "active_entries"):
        for e in scope.active_entries(now=now):
            armed.append({"kind": e.kind, "value": _disp(e.value), "job": e.job,
                          "expires": e.expires})

    techniques = sorted({tid for l in lines for tid in l.attack_ids})
    return {
        "engagement": engagement,
        "operator": operator,
        "generated_at": now,
        "window": list(window) if window else None,
        "integrity": "FLAGGED" if unauthorized else "CLEAN",
        "unauthorized_count": len(unauthorized),
        "authorization": {"armed": armed, "summary": scope_summary},
        "activity": [l.to_dict() for l in lines],
        "techniques_exercised": techniques,
        "findings": dex_summary or {},
        "coverage": range_report or {},
        "sanitized": bool(sanitize),
        "integrity_note": ("all recorded actions were within the authorized scope"
                           if not unauthorized else
                           f"{len(unauthorized)} action(s) were OUT OF SCOPE — see ⚠ rows"),
    }


def _fmt_ts(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(ts))
    except (ValueError, OSError):
        return str(ts)


def render_markdown(report: dict) -> str:
    """Render the report dict as a clean Markdown deliverable."""
    r = report
    out: List[str] = []
    out.append(f"# Engagement report — {r['engagement']}")
    out.append("")
    out.append(f"- **Operator:** {r['operator']}")
    out.append(f"- **Generated:** {_fmt_ts(r['generated_at'])}")
    if r.get("window"):
        w = r["window"]
        out.append(f"- **Window:** {_fmt_ts(w[0])} – {_fmt_ts(w[1])}")
    out.append(f"- **Integrity:** {r['integrity']} — {r['integrity_note']}")
    if r.get("sanitized"):
        out.append("- **Note:** identifiers pseudonymized for sharing (sanitized)")
    out.append("")

    out.append("## Authorization (central Scope)")
    armed = r["authorization"]["armed"]
    if armed:
        out.append("| Kind | Target | Job | Expires |")
        out.append("|---|---|---|---|")
        for e in armed:
            exp = "never" if not e.get("expires") else _fmt_ts(e["expires"])
            out.append(f"| {e['kind']} | {e['value']} | {e.get('job') or '—'} | {exp} |")
    else:
        out.append("_No targets were armed._")
    out.append("")

    out.append("## Activity (chain of authorization)")
    out.append("| Time | Action | ATT&CK | Phase | Target | Authorized | Result |")
    out.append("|---|---|---|---|---|---|---|")
    for l in r["activity"]:
        auth = "yes" if l["authorized"] else "⚠ UNAUTHORIZED"
        atk = ",".join(l["attack_ids"]) or "—"
        out.append(f"| {_fmt_ts(l['ts'])} | {l['action']} | {atk} | {l['phase']} | "
                   f"{l['target']} | {auth} | {l['result'] or '—'} |")
    out.append("")

    if r["techniques_exercised"]:
        out.append("## ATT&CK techniques exercised")
        out.append(", ".join(r["techniques_exercised"]))
        out.append("")

    if r.get("findings"):
        out.append("## Findings")
        for k, v in r["findings"].items():
            out.append(f"- **{k}:** {v}")
        out.append("")

    if r.get("coverage") and r["coverage"].get("reason"):
        out.append("## Detection coverage (purple)")
        out.append(r["coverage"]["reason"])
        if r["coverage"].get("gaps"):
            out.append("")
            out.append("Gaps:")
            for g in r["coverage"]["gaps"]:
                out.append(f"- {g['action']} ({','.join(g.get('attack', []))}) — no expected detector fired")
        out.append("")

    out.append("---")
    out.append("_Generated by redux. Real data only; every action carries its "
               "authorization check and reason. Out-of-scope actions are flagged, not hidden._")
    return "\n".join(out)
