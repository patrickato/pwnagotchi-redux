#!/usr/bin/env python3
"""One-command, read-only Redux field acceptance from the trusted WSL laptop.

Uses the already-established SSH key. Does not reflash, change Wi-Fi,
restart services, or retrieve raw captures/caplets. Never claims that an
active daemon proves genuine RF reception or valid handshakes.
"""
from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import re
import subprocess
import sys

FIELDS = (
    "captures_ext4", "redux-live.service", "redux.service",
    "redux-capture-ingest.timer", "live_state", "capture_radio", "dashboard",
    "bettercap_api_probe", "events_seen", "handed_off",
)
COUNTRY = re.compile(r"\bcountry ([A-Z]{2}|00):")
SOFT = re.compile(r"Soft blocked:\s*(yes|no)", re.I)
MONITOR = re.compile(r"(?m)^\s*type monitor\s*$")


def parse(report):
    """Parse only known, bounded keys; log content is never echoed."""
    result = {}
    for line in report.splitlines():
        if line.startswith("== "):
            continue
        if ": " in line:
            key, value = line.split(": ", 1)
            if key in FIELDS and key not in result:
                result[key] = value.strip()[:100]
    result["regulatory_country"] = (
        COUNTRY.search(report).group(1) if COUNTRY.search(report) else "")
    wifi_block = report.partition("== RF kill ==")[2].partition("== Regulatory domain ==")[0]
    blocks = re.split(r"(?m)^\d+:\s*", wifi_block)
    relevant = [block for block in blocks if "Wireless LAN" in block]
    found = SOFT.search(relevant[0]) if relevant else None
    result["wifi_soft_blocked"] = found.group(1).lower() if found else ""
    interfaces = report.partition("== Interfaces ==")[2].partition(
        "== Bettercap last output")[0]
    result["monitor_mode"] = bool(MONITOR.search(interfaces))
    return result


def verdicts(data, expected_country):
    checks = []
    def check(name, status, why):
        checks.append((name, status, why))

    check("REDUXCAP ext4", "PASS" if data.get("captures_ext4") == "True" else "FAIL",
          "dedicated writable capture partition")
    for unit in ("redux.service", "redux-live.service", "redux-capture-ingest.timer"):
        check(unit, "PASS" if data.get(unit) == "active" else "FAIL",
              "systemd " + data.get(unit, "unobserved"))
    state = data.get("live_state", "unobserved")
    check("Live capture supervisor", "PASS" if state == "running" else "FAIL",
          state)
    check("Doctor dashboard", "PASS" if data.get("dashboard") == "True" else "FAIL",
          "reported live listener state")
    wifi = data.get("wifi_soft_blocked")
    check("Wi-Fi software block", "PASS" if wifi == "no" else
          "FAIL" if wifi == "yes" else "UNKNOWN",
          "soft blocked: " + (wifi or "not observed"))
    current_country = data.get("regulatory_country", "")
    check("Regulatory country", "PASS" if current_country == expected_country else
          "FAIL" if current_country else "UNKNOWN",
          (current_country or "unknown") + "; expected " + expected_country)
    check("Monitor radio", "PASS" if data.get("capture_radio") and
          data["capture_radio"] not in ("unobserved", "None") and
          data.get("monitor_mode") else "FAIL",
          "radio: " + data.get("capture_radio", "unknown"))
    api = data.get("bettercap_api_probe", "")
    check("Bettercap REST auth", "PASS" if api.startswith("HTTP 401") else
          "FAIL" if api.startswith(("HTTP 200", "HTTP 400", "HTTP 403", "unavailable"))
          else "UNKNOWN", api or "not measured")
    for name, key in (("Observed radio events", "events_seen"),
                      ("Closed capture handoff", "handed_off")):
        raw = data.get(key, "")
        count = int(raw) if raw.isdecimal() else None
        check(name, "PASS" if count is not None and count > 0 else "UNKNOWN",
              f"{count} observed" if count is not None else "not reported")
    return checks


def fetch_report(ip, key, *, runner=subprocess.run):
    args = [
        "ssh", "-i", str(key), "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8",
        f"pi@{ip}", "sudo", "-n", "redux-report", "--full",
    ]
    try:
        p = runner(args, capture_output=True, text=True, timeout=40,
                   check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"SSH diagnostic unavailable ({type(exc).__name__})") from exc
    if p.returncode:
        raise RuntimeError(
            "SSH or remote diagnostic failed; confirm the Pi is reachable, "
            "its host key was already verified, and sudo -n works"
        )
    if not p.stdout.startswith("REDUX FIELD REPORT"):
        raise RuntimeError("Unexpected remote report; no device identity assumed")
    return p.stdout


def main(argv=None, *, runner=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="trusted Pi private IPv4 address")
    parser.add_argument("--country", required=True,
                        help="operator-confirmed physical two-letter Wi-Fi country")
    parser.add_argument("--key", type=Path,
                        default=Path.home() / ".ssh/redux_field_ed25519")
    args = parser.parse_args(argv)
    try:
        address = ipaddress.IPv4Address(args.host)
        if not address.is_private or address.is_loopback or address.is_link_local:
            raise ValueError("must be a private LAN address, not localhost")
        if not re.fullmatch(r"[A-Z]{2}", args.country) or args.country == "ZZ":
            raise ValueError("country must be your real two-letter regulatory domain")
        if not args.key.is_file():
            raise ValueError(f"SSH field key not found: {args.key}")
        report = fetch_report(address, args.key, runner=runner)
    except (ValueError, RuntimeError) as exc:
        print(f"Redux field acceptance unavailable: {exc}", file=sys.stderr)
        return 2

    checks = verdicts(parse(report), args.country)
    print("REDUX FIELD ACCEPTANCE (read only; no radio changes)")
    print(f"Target: {address} | operator country: {args.country}")
    for name, state, why in checks:
        print(f"  {state:7} {name:26} {why}")
    passed = sum(state == "PASS" for _, state, _ in checks)
    failed = sum(state == "FAIL" for _, state, _ in checks)
    unknown = sum(state == "UNKNOWN" for _, state, _ in checks)
    print(f"\n{passed} passed; {failed} failed; {unknown} not yet verified.")
    if not failed and not unknown:
        print("Field signals observed; controlled handshake conversion and "
              "TFT acceptance are separate gates.")
        return 0
    print("Not a release approval. Fix failures and verify unknown physical gates.")
    return 1 if failed else 3


if __name__ == "__main__":
    raise SystemExit(main())
