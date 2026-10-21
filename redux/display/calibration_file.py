"""Bounded offline five-point calibration file loader for the opt-in TFT UI.

A candidate mapping is not physical approval. The CLI separately requires an
operator confirmation before ever opening a touch input device.
"""
from __future__ import annotations

import json
import os
import stat

from .calibration import TouchPoint, evaluate


def load_calibration(path: str, *, opener=os.open, reader=os.read,
                     fstat=os.fstat, closer=os.close):
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        raise ValueError("absolute calibration file path required")
    fd = opener(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 8192 or info.st_size < 2:
            raise ValueError("invalid calibration file type or size")
        data = reader(fd, 8193)
        if len(data) > 8192:
            raise ValueError("oversized calibration")
    finally:
        closer(fd)
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid calibration JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "points"} or payload["schema"] != 1:
        raise ValueError("unsupported calibration schema")
    raw = payload["points"]
    if not isinstance(raw, list) or len(raw) < 5 or len(raw) > 32:
        raise ValueError("five to 32 measured samples required")
    points = []
    for row in raw:
        if not isinstance(row, dict) or set(row) != {"raw_x", "raw_y", "pixel_x", "pixel_y"}:
            raise ValueError("invalid sample")
        points.append(TouchPoint(**row))
    calibration = evaluate(points)
    if calibration is None:
        raise ValueError("touch calibration inconsistent or incomplete")
    return calibration
