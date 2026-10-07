"""redux.report — the engagement report deliverable.

Aggregates the central Scope (authorization), the glass-box reason on every
action, the ATT&CK/PTES tags, and optionally the Dex findings and a purple Range
coverage score into a chain-of-authorization report (dict + Markdown). Its
integrity rests on surfacing any out-of-scope action as ⚠ UNAUTHORIZED rather than
hiding it. Optional ghost sanitization lets a report be shared without leaking
real targets.
"""
from .engagement import (
    EngagementAction, ReportLine, build_report, render_markdown,
)

__all__ = ["EngagementAction", "ReportLine", "build_report", "render_markdown"]
