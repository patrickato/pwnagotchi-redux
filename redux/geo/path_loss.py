"""RSSI path-loss distance estimate (log-distance model)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PathLossEstimate:
    distance_m: float
    rssi: int
    tx_power_dbm: float
    n: float
    reason: str


def rssi_to_distance_m(
    rssi: int,
    *,
    tx_power_dbm: float = -20.0,
    n: float = 2.0,
    d0_m: float = 1.0,
) -> PathLossEstimate:
    """Log-distance: rssi ≈ tx - 10*n*log10(d/d0).

    Rough, environment-dependent — glass-box reason states the model.
    """
    if n <= 0:
        raise ValueError("path-loss exponent n must be > 0")
    # d = d0 * 10^((tx - rssi)/(10n))
    exponent = (tx_power_dbm - float(rssi)) / (10.0 * n)
    dist = d0_m * (10.0 ** exponent)
    dist = max(0.1, min(dist, 50_000.0))  # clamp absurd values
    reason = (
        f"path-loss estimate: rssi={rssi} dBm, tx={tx_power_dbm} dBm, "
        f"n={n} → ~{dist:.1f} m (log-distance model)"
    )
    return PathLossEstimate(
        distance_m=dist,
        rssi=rssi,
        tx_power_dbm=tx_power_dbm,
        n=n,
        reason=reason,
    )
