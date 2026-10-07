"""Replay / Ghost — record a session and replay a sanitized copy of it.

`redux run/web --replay` already consume a recorded bettercap event list. This
package adds the two honest bookends: a recorder that captures the live stream to
that same schema, and a sanitizer that produces a shareable *ghost* of a
recording — real MACs/SSIDs pseudonymized, location dropped, timing/structure
preserved — so you can demo or review a session without leaking real recon data.
"""
from .ghost import (
    GhostMapper, GhostRecorder, sanitize_events, sanitize_file,
)

__all__ = ["GhostMapper", "GhostRecorder", "sanitize_events", "sanitize_file"]
