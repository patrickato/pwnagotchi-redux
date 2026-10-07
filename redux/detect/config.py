"""redux.detect config — DEFAULTS + _opt (plugin-style).

Options are read from a plain dict (or empty). Missing keys fall back to
DEFAULTS. Section name for future TOML wiring is the file basename: `detect`.
"""
from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Optional

SECTION = "detect"

DEFAULTS: dict = {
    # AlertBus
    "dedup_window_s": 60.0,
    "escalate_after": 3,
    "escalate_to": "critical",
    # Core detectors
    "deauth_window_s": 5.0,
    "deauth_threshold": 20,
    "beacon_spam_window_s": 10.0,
    "beacon_spam_unique_ssid_threshold": 30,
    "beacon_spam_unique_bssid_threshold": 40,
    "sweep_window_s": 30.0,
    "sweep_new_bssid_threshold": 25,
    # Karma / pineapple / PNL
    "karma_window_s": 15.0,
    "karma_ssid_threshold": 8,
    "pineapple_window_s": 20.0,
    "pineapple_probe_ssid_threshold": 10,
    "pineapple_beacon_ssid_threshold": 15,
    "pnl_loud_threshold": 8,
    # WPS / handshake
    "wps_window_s": 30.0,
    "wps_attempt_threshold": 12,
    "wps_nack_threshold": 6,
    "handshake_window_s": 30.0,
    # BLE flood
    "ble_flood_window_s": 5.0,
    "ble_flood_unique_addr_threshold": 20,
}


def _opt(options: Optional[Mapping[str, Any]], key: str) -> Any:
    if options is not None and key in options:
        return options[key]
    return DEFAULTS[key]


def merge_options(overrides: Optional[Mapping[str, Any]] = None) -> dict:
    out: MutableMapping[str, Any] = dict(DEFAULTS)
    if overrides:
        out.update(overrides)
    return dict(out)
