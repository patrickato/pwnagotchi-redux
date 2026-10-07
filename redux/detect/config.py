"""redux.detect config — DEFAULTS + _opt (plugin-style).

Options are read from a plain dict (or empty). Missing keys fall back to
DEFAULTS. Section name for future TOML wiring is the file basename: `detect`.
"""
from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Optional

# Section basename for future config loaders (AGENTS.md convention).
SECTION = "detect"

DEFAULTS: dict = {
    # AlertBus
    "dedup_window_s": 60.0,
    "escalate_after": 3,  # repeats before severity steps up
    "escalate_to": "critical",
    # Detector thresholds (shared reference; individual detectors may override)
    "deauth_window_s": 5.0,
    "deauth_threshold": 20,
    "beacon_spam_window_s": 10.0,
    "beacon_spam_unique_ssid_threshold": 30,
    "beacon_spam_unique_bssid_threshold": 40,
    "sweep_window_s": 30.0,
    "sweep_new_bssid_threshold": 25,
}


def _opt(options: Optional[Mapping[str, Any]], key: str) -> Any:
    """Read one option; DEFAULTS when missing or options is None."""
    if options is not None and key in options:
        return options[key]
    return DEFAULTS[key]


def merge_options(overrides: Optional[Mapping[str, Any]] = None) -> dict:
    """Return a full options dict: DEFAULTS with overrides applied."""
    out: MutableMapping[str, Any] = dict(DEFAULTS)
    if overrides:
        out.update(overrides)
    return dict(out)
