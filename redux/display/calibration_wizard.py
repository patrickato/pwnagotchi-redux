"""Offline conversion of five operator-labeled raw taps into a candidate mapping.

The input is an existing redux-display-diag JSON report. No input devices,
framebuffers, wireless interfaces, remote SSH or system settings are accessed.
An explicit tap-order confirmation is required and does not imply acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import stat

from .calibration import TouchPoint, evaluate

TARGETS = ((0, 0), (479, 0), (0, 319), (479, 319), (239.5, 159.5))
_FILENAME = "touch-calibration.json"
_MAX_INPUT = 65536


def read_report(path: str) -> dict:
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValueError("absolute diagnostic file required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 2 <= info.st_size <= _MAX_INPUT:
            raise ValueError("invalid diagnostic file")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(_MAX_INPUT + 1)
    finally:
        os.close(fd)
    if len(raw) > _MAX_INPUT:
        raise ValueError("oversized diagnostic")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid diagnostic JSON") from exc
    if not isinstance(result, dict):
        raise ValueError("invalid diagnostic structure")
    return result


def candidate(report: dict) -> tuple[dict, object]:
    """Require five completed ordered samples, independently check axis bounds."""
    if not isinstance(report, dict) or report.get("schema") != 1:
        raise ValueError("unsupported diagnostic report")
    sample = report.get("touch_sample")
    if not isinstance(sample, dict) or sample.get("status") != "observed":
        raise ValueError("no completed raw touch sample")
    if type(sample.get("dropped_syncs")) is not int or sample["dropped_syncs"] != 0:
        raise ValueError("lost touch synchronization")
    raw = sample.get("taps")
    if not isinstance(raw, list) or len(raw) != 5:
        raise ValueError("exactly five ordered taps required")
    bounds = sample.get("raw_axis_bounds")
    if not isinstance(bounds, dict):
        raise ValueError("raw axis bounds unavailable")
    for axis in ("x", "y"):
        item = bounds.get(axis)
        if (not isinstance(item, dict) or
                type(item.get("min")) is not int or type(item.get("max")) is not int or
                item["min"] >= item["max"]):
            raise ValueError("invalid measured axis bounds")
    points = []
    for item, (pixel_x, pixel_y) in zip(raw, TARGETS):
        if not isinstance(item, dict):
            raise ValueError("invalid raw touch sample")
        x, y = item.get("raw_x"), item.get("raw_y")
        if type(x) is not int or type(y) is not int:
            raise ValueError("raw coordinates must be measured integers")
        if (not bounds["x"]["min"] <= x <= bounds["x"]["max"] or
                not bounds["y"]["min"] <= y <= bounds["y"]["max"]):
            raise ValueError("sample outside measured controller bounds")
        points.append(TouchPoint(x, y, pixel_x, pixel_y))
    fit = evaluate(points)
    if fit is None:
        raise ValueError("five-point mapping inconsistent or incomplete")
    payload = {"schema": 1, "points": [
        {"raw_x": p.raw_x, "raw_y": p.raw_y,
         "pixel_x": p.pixel_x, "pixel_y": p.pixel_y}
        for p in points
    ]}
    return payload, fit


def save_candidate(directory: str, payload: dict) -> str:
    """No-overwrite, atomic, private output in an existing absolute directory."""
    if not isinstance(directory, str) or not Path(directory).is_absolute():
        raise ValueError("absolute output directory required")
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(data) > 8192:
        raise ValueError("candidate too large")
    dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        name = f".redux-calibration-{secrets.token_hex(12)}.tmp"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=dirfd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(name, _FILENAME, src_dir_fd=dirfd, dst_dir_fd=dirfd,
                    follow_symlinks=False)
        finally:
            os.unlink(name, dir_fd=dirfd)
        os.fsync(dirfd)
        return _FILENAME
    finally:
        os.close(dirfd)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline candidate calibration from five measured taps")
    parser.add_argument("--report", required=True, help="absolute redux-display-diag JSON file")
    parser.add_argument("--output-dir", required=True, help="existing absolute output directory")
    parser.add_argument("--confirm-order", action="store_true",
                        help="confirm physical taps were top-left, top-right, bottom-left, bottom-right, center")
    args = parser.parse_args(argv)
    if not args.confirm_order:
        parser.error("physical five-point tap order must be confirmed")
    try:
        payload, fit = candidate(read_report(args.report))
        name = save_candidate(args.output_dir, payload)
    except (OSError, ValueError, TypeError) as exc:
        parser.error(f"calibration refused: {type(exc).__name__}")
    print(json.dumps({"file": name, "candidate_residual_px": fit.maximum_error_px,
                      "swap_axes": fit.swap_axes, "reverse_x": fit.reverse_x,
                      "reverse_y": fit.reverse_y, "physical_touch_verified": False,
                      "hardware_acceptance": "pending_operator_verification"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
