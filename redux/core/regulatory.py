#!/usr/bin/env python3
"""Boot-time Wi-Fi regulatory setup for an operator-configured Redux image.

No guessed country, no monitor-mode changes, no packets transmitted. Only
apply the baked two-letter country and unblock the *software* Wi-Fi rfkill
after the kernel confirms the regulatory domain. Never overrides hard rfkill.
A one-shot service applies the country; the live supervisor independently
checks it before launching Bettercap, while keeping Doctor diagnostics online.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

COUNTRY = re.compile(r"^[A-Z]{2}$")
GLOBAL = re.compile(r"(?m)^country ([A-Z]{2}|00):")
SOFT = re.compile(r"(?im)^\s*Soft blocked:\s*(yes|no)\s*$")
HARD = re.compile(r"(?im)^\s*Hard blocked:\s*(yes|no)\s*$")


class RegulatoryError(RuntimeError):
    pass


def read_country(path: Path) -> str:
    if path.is_symlink():
        raise RegulatoryError("regulatory country file must not be a symlink")
    try:
        st = path.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_size > 8 or st.st_mode & 0o022:
            raise RegulatoryError("regulatory country file permissions/content invalid")
        country = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise RegulatoryError("regulatory country file unavailable") from exc
    if not COUNTRY.fullmatch(country) or country in ("ZZ", "AA"):
        raise RegulatoryError("configured regulatory country must be an operator-selected two-letter code")
    return country


def current_country(value: str) -> str:
    # Global is at the top; phy-specific self-managed domains must not be
    # confused with the global policy the boot script just requested.
    global_section = value.split("phy#", 1)[0]
    match = GLOBAL.search(global_section)
    return match.group(1) if match else ""


def wireless_unblocked(value: str) -> bool:
    sections = re.split(r"(?m)^\d+:\s*", value)
    wifi = [section for section in sections if "Wireless LAN" in section]
    return bool(wifi) and all(
        (soft := SOFT.search(section)) is not None
        and soft.group(1).lower() == "no"
        and (hard := HARD.search(section)) is not None
        and hard.group(1).lower() == "no"
        for section in wifi
    )


def run_command(command, *, runner=subprocess.run):
    try:
        result = runner(command, capture_output=True, text=True, timeout=6,
                        check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RegulatoryError(f"{command[0]} is unavailable") from exc
    return result


def verify_country(country: str, *, runner=subprocess.run) -> None:
    """Read-only preflight for the live engine; never changes radio state."""
    reg = run_command(["iw", "reg", "get"], runner=runner)
    if reg.returncode or current_country(reg.stdout) != country:
        raise RegulatoryError("selected regulatory country is not active")
    blocks = run_command(["rfkill", "list"], runner=runner)
    if blocks.returncode or not wireless_unblocked(blocks.stdout):
        raise RegulatoryError("Wi-Fi rfkill is blocked or could not be verified")


def configure(country: str, *, runner=subprocess.run, wait=time.sleep,
              attempts=5):
    if not COUNTRY.fullmatch(country) or country in ("ZZ", "AA"):
        raise RegulatoryError("invalid operator regulatory country")
    set_country = run_command(["iw", "reg", "set", country], runner=runner)
    if set_country.returncode:
        raise RegulatoryError("kernel rejected selected regulatory country")
    # An asynchronous cfg80211 update may not be visible instantly.
    observed = ""
    for n in range(attempts):
        result = run_command(["iw", "reg", "get"], runner=runner)
        observed = current_country(result.stdout) if result.returncode == 0 else ""
        if observed == country:
            break
        if n + 1 < attempts:
            wait(1)
    if observed != country:
        raise RegulatoryError("kernel did not confirm selected regulatory country")
    # Only after successful regulatory confirmation may rfkill be unblocked.
    device = run_command(["rfkill", "list"], runner=runner)
    if device.returncode or "Wireless LAN" not in device.stdout:
        raise RegulatoryError("Wi-Fi rfkill device not ready")
    result = run_command(["rfkill", "unblock", "wifi"], runner=runner)
    if result.returncode:
        raise RegulatoryError("Wi-Fi soft rfkill could not be cleared")
    status = run_command(["rfkill", "list"], runner=runner)
    if status.returncode or not wireless_unblocked(status.stdout):
        raise RegulatoryError("Wi-Fi software block remains active")
    return country


def main(argv=None, *, runner=subprocess.run, wait=time.sleep):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country-file", type=Path,
                        default=Path("/etc/redux/regulatory-country"))
    args = parser.parse_args(argv)
    try:
        country = read_country(args.country_file)
        configure(country, runner=runner, wait=wait)
    except RegulatoryError as exc:
        print(f"Redux Wi-Fi country configuration failed: {exc}", file=sys.stderr)
        return 1
    print(f"Redux Wi-Fi regulatory country {country} confirmed; software block clear")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
