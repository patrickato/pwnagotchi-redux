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
    "bettercap_api_probe", "events_seen", "handed_off", "capture_link_type",
    "checkpoint_freshness",
)
COUNTRY = re.compile(r"(?m)^\s*country\s+([A-Z]{2}|00):")
SOFT = re.compile(r"Soft blocked:\s*(yes|no)", re.I)
HARD = re.compile(r"Hard blocked:\s*(yes|no)", re.I)
MONITOR = re.compile(r"(?m)^\s*type monitor\s*$")


def parse(report):
    """Parse only known, bounded keys; log content is never echoed."""
    result = {}
    for line in report.splitlines():
        if line.startswith("== "):
            break  # Only the report header supplies measured fields, never log text.
        if ": " in line:
            key, value = line.split(": ", 1)
            if key in FIELDS and key not in result:
                result[key] = value.strip()[:100]
    # Restrict country detection to the global cfg80211 section, not a
    # PHY-specific self-managed domain or arbitrary diagnostic log lines.
    reg = report.partition("== Regulatory domain ==")[2].partition(
        "== Interfaces ==")[0].split("phy#", 1)[0]
    country = COUNTRY.search(reg)
    result["regulatory_country"] = country.group(1) if country else ""

    wifi_block = report.partition("== RF kill ==")[2].partition(
        "== Regulatory domain ==")[0]
    blocks = re.split(r"(?m)^\d+:\s*", wifi_block)
    wireless = [block for block in blocks if "Wireless LAN" in block]

    def block_state(pattern):
        # Any known block is a failure; missing data can never pass.
        matches = [pattern.search(block) for block in wireless]
        values = [match.group(1).lower() for match in matches if match]
        if "yes" in values:
            return "yes"
        return "no" if matches and len(values) == len(matches) else ""

    result["wifi_soft_blocked"] = block_state(SOFT)
    result["wifi_hard_blocked"] = block_state(HARD)

    interfaces = report.partition("== Interfaces ==")[2].partition(
        "== Bettercap last output")[0]
    # Match the selected interface's own 'iw dev' block. Monitor mode
    # on an unrelated radio is not a live capture-radio PASS.
    parts = re.split(r"(?m)^\s*Interface\s+(\S+)\s*$", interfaces)
    result["monitor_ifaces"] = {
        parts[i] for i in range(1, len(parts), 2)
        if MONITOR.search(parts[i + 1])
    }
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
    freshness = data.get("checkpoint_freshness", "unobserved")
    check("Live checkpoint freshness", "PASS" if freshness == "fresh" else
          "FAIL" if freshness in {"stale", "future", "invalid"} else "UNKNOWN",
          freshness + "; saved state alone does not prove a live supervisor")
    check("Doctor dashboard", "PASS" if data.get("dashboard") == "True" else "FAIL",
          "reported live listener state")
    for title, key in (("Wi-Fi software block", "wifi_soft_blocked"),
                       ("Wi-Fi hardware block", "wifi_hard_blocked")):
        blocked = data.get(key)
        check(title, "PASS" if blocked == "no" else
              "FAIL" if blocked == "yes" else "UNKNOWN",
              "blocked: " + (blocked or "not observed"))
    current_country = data.get("regulatory_country", "")
    check("Regulatory country", "PASS" if current_country == expected_country else
          "FAIL" if current_country else "UNKNOWN",
          (current_country or "unknown") + "; expected " + expected_country)
    radio = data.get("capture_radio", "")
    check("Monitor radio", "PASS" if radio and radio in
          data.get("monitor_ifaces", set()) else "FAIL",
          "selected: " + (radio or "unknown") +
          "; monitors: " + (", ".join(sorted(data.get("monitor_ifaces", set()))) or "none"))
    link = data.get("capture_link_type", "")
    check("Monitor packet link type", "PASS" if link in ("801", "803") else
          "FAIL" if link.isdecimal() else "UNKNOWN",
          "ARPHRD " + link + "; expected 802.11 (801) or radiotap (803)"
          if link.isdecimal() else "not measured; update the field report helper")
    api = data.get("bettercap_api_probe", "")
    check("Bettercap REST auth", "PASS" if api.startswith("HTTP 401") else
          "FAIL" if api.startswith(("HTTP 200", "HTTP 400", "HTTP 403", "unavailable"))
          else "UNKNOWN", api or "not measured")
    for name, key in (("Observed runtime events", "events_seen"),
                      ("Closed capture handoff", "handed_off")):
        raw = data.get(key, "")
        count = int(raw) if raw.isdecimal() else None
        check(name, "PASS" if count is not None and count > 0 else "UNKNOWN",
              f"{count} observed" if count is not None else "not reported")
    return checks



def status_verdicts(data, expected_country=None):
    """Reuse acceptance policy for the sanitized, unprivileged status path.

    Only fields measured by that endpoint are eligible for PASS/FAIL. Missing
    storage/systemd/regulatory/ownership/RF observations remain UNKNOWN. No
    full report, sudo command or radio query is requested by this adapter.
    """
    allowed = {"live_state": {"running", "starting", "starting_engine", "rotating",
               "degraded", "telemetry_degraded", "storage_paused", "stopped", "lease_blocked", "termination_pending"},
               "dashboard": {"True", "False"},
               "checkpoint_freshness": {"fresh", "stale", "future", "invalid"}}
    clean = {}
    if isinstance(data, dict):
        for key, states in allowed.items():
            if isinstance(data.get(key), str) and data[key] in states:
                clean[key] = data[key]
        for key in ("events_seen", "handed_off", "capture_link_type"):
            raw = data.get(key)
            if isinstance(raw, str) and re.fullmatch(r"[0-9]{1,19}", raw):
                clean[key] = raw
    measured = {"Live capture supervisor": "live_state", "Doctor dashboard": "dashboard",
                "Live checkpoint freshness": "checkpoint_freshness",
                "Monitor packet link type": "capture_link_type",
                "Observed runtime events": "events_seen", "Closed capture handoff": "handed_off"}
    result = []
    stale = clean.get("checkpoint_freshness") != "fresh"
    for name, state, reason in verdicts(clean, expected_country or "unconfigured"):
        key = measured.get(name)
        if key is None or key not in clean:
            state, reason = "UNKNOWN", "not measured by unprivileged status endpoint"
        elif stale and name != "Live checkpoint freshness":
            state, reason = "UNKNOWN", "runtime checkpoint stale or invalid"
        if name == "Regulatory country" and isinstance(expected_country, str) and re.fullmatch(r"[A-Z]{2}", expected_country):
            reason += "; operator expects " + expected_country
        if name == "Observed runtime events" and state == "PASS":
            reason += "; runtime events alone do not prove RF reception"
        result.append((name, state, reason))
    return result


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
