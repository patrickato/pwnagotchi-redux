#!/usr/bin/env python3
"""Read-only field status qualification via the existing trusted SSH connection.

Runs a bounded standard-library HTTP probe *on the Pi* and returns only
allowlisted health/telemetry fields. Never exports the raw status response,
credentials, nearby network identifiers, captures, or caplets.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import shlex
from pathlib import Path
import subprocess
import sys

# Embedded program executes on the Pi with its system Python, no installed
# Redux development dependencies or sudo privileges required.
REMOTE = r'''
import json, math, sys, urllib.request
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:8080/api/status", timeout=3) as response:
        payload = response.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError("response too large")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("invalid status root")
    doctor = data.get("doctor")
    if not isinstance(doctor, dict):
        doctor = {}
    findings = doctor.get("findings")
    if not isinstance(findings, list):
        findings = []
    areas = {}
    for item in findings[:100]:
        if isinstance(item, dict) and item.get("area") in ("thermal/power", "radio", "capture", "location", "scope"):
            severity = item.get("status")
            if severity in ("ok", "attention", "degraded", "action", "unknown"):
                areas[item["area"]] = severity
    # Deliberately exclude finding reasons: they can include access point names.
    out = {"doctor_overall": doctor.get("overall") if doctor.get("overall") in
           ("ok", "attention", "degraded", "action", "unknown") else None,
           "areas": areas, "telemetry": None}
    # The collector is added to the live status by the current development
    # branch. Never guess where an older deployed image might expose it.
    candidates = [(key, data.get(key)) for key in ("hardware_telemetry", "resource_telemetry", "telemetry")]
    runtime = data.get("runtime")
    if isinstance(runtime, dict):
        candidates.extend((("runtime." + key), runtime.get(key)) for key in
                          ("hardware_telemetry", "resource_telemetry", "telemetry"))
    for key, obj in candidates:
        if isinstance(obj, dict) and isinstance(obj.get("fresh"), bool) and isinstance(obj.get("values"), dict):
            values = obj["values"]
            allow = ("cpu_temp_c", "cpu_load_pct", "ram_pct", "cpu_frequency_mhz", "throttled_mask",
                     "battery_pct", "on_battery")
            clean = {}
            for name in allow:
                value = values.get(name)
                if value is None or isinstance(value, bool) and name == "on_battery":
                    clean[name] = value
                elif isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
                    clean[name] = value
                else:
                    clean[name] = None
            out["telemetry"] = {"source": key, "fresh": obj["fresh"], "values": clean}
            break
    print(json.dumps(out, sort_keys=True, allow_nan=False))
except Exception as exc:
    print(json.dumps({"error": type(exc).__name__}))
    sys.exit(2)
'''


def assess(value):
    """Generate stable verdicts from allowlisted measurements only."""
    checks = []
    if not isinstance(value, dict) or "error" in value:
        return [("Local status endpoint", "FAIL", "unavailable or invalid")]
    overall = value.get("doctor_overall")
    checks.append(("Doctor report", "PASS" if overall in
                   ("ok", "attention", "degraded", "action", "unknown") else "FAIL",
                   str(overall) if overall else "missing"))
    areas = value.get("areas") if isinstance(value.get("areas"), dict) else {}
    thermal = areas.get("thermal/power")
    checks.append(("Doctor thermal assessment", "PASS" if thermal in
                   ("ok", "attention", "degraded", "action") else "UNKNOWN",
                   thermal or "not reported"))
    telemetry = value.get("telemetry")
    if not isinstance(telemetry, dict):
        checks.append(("Telemetry integration", "UNKNOWN", "not exposed in supported status locations"))
        return checks
    checks.append(("Telemetry freshness", "PASS" if telemetry.get("fresh") is True else
                   "FAIL" if telemetry.get("fresh") is False else "UNKNOWN",
                   str(telemetry.get("fresh"))))
    values = telemetry.get("values") if isinstance(telemetry.get("values"), dict) else {}
    for key in ("cpu_temp_c", "cpu_load_pct", "ram_pct", "cpu_frequency_mhz"):
        v = values.get(key)
        checks.append((key, "PASS" if isinstance(v, (int, float)) and not isinstance(v, bool) else
                       "UNKNOWN", str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else "unavailable"))
    return checks


def fetch(ip, key, *, runner=subprocess.run):
    argv = ["ssh", "-i", str(key), "-o", "BatchMode=yes", "-o",
            "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8",
            f"pi@{ip}", "python3 -c " + shlex.quote(REMOTE)]
    try:
        result = runner(argv, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(type(exc).__name__) from exc
    if result.returncode:
        raise RuntimeError("remote read-only probe failed (SSH, Python, or endpoint)")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("invalid remote JSON") from exc


def main(argv=None, *, runner=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.12.115", help="trusted Pi LAN IPv4")
    parser.add_argument("--key", type=Path, default=Path.home() / ".ssh/redux_field_ed25519")
    args = parser.parse_args(argv)
    try:
        ip = ipaddress.IPv4Address(args.host)
        if not ip.is_private or ip.is_loopback or ip.is_link_local:
            raise ValueError("expected a trusted private LAN IPv4 address")
        if not args.key.is_file():
            raise ValueError("SSH key file not found")
        checks = assess(fetch(ip, args.key, runner=runner))
    except (RuntimeError, ValueError) as exc:
        print("Field status unavailable: " + str(exc), file=sys.stderr)
        return 2
    print("REDUX FIELD STATUS — READ ONLY")
    print("No radio commands, service restarts, or installation")
    for name, result, detail in checks:
        print(f"  {result:7} {name:29} {detail}")
    failures = sum(result == "FAIL" for _, result, _ in checks)
    unknown = sum(result == "UNKNOWN" for _, result, _ in checks)
    print(f"Results: {len(checks)-failures-unknown} PASS, {failures} FAIL, {unknown} UNKNOWN")
    return 1 if failures else 3 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
