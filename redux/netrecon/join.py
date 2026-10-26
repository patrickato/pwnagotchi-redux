"""Wi-Fi JOIN — the missing link that turns a cracked key into LAN access.

The kill-chain used to stop at a recovered PSK: there was nothing to actually
*associate* to the AP and get onto its network, so the pivot phases (scan → cred →
loot) had no subnet to work. `WifiJoiner` closes that: given an SSID and a recovered
pre-shared key, it brings up a MANAGED client association on an uplink-capable
interface (never the monitor capture radio), takes a DHCP lease, and reports the
subnet CIDR + gateway the pivot then operates over.

Strictly LAB: this is the "get on the Wi-Fi, then own the LAN" step, and it only
runs inside the Operator behind the Scope + posture + lab gates. Pure/testable —
the subprocess runner is injected, so command construction, lease parsing, and CIDR
derivation are verified with no radio and no DHCP server.
"""
from __future__ import annotations

import ipaddress
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

_IFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
# A conservative SSID guard: no control chars / embedded NULs reaching argv.
_SSID_BAD = re.compile(r"[\x00-\x1f]")


@dataclass(frozen=True)
class JoinResult:
    joined: bool
    iface: str
    ssid: str
    ip: str = ""
    subnet: str = ""        # CIDR the pivot scans, e.g. "192.168.1.0/24"
    gateway: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {"joined": self.joined, "iface": self.iface, "ssid": self.ssid,
                "ip": self.ip, "subnet": self.subnet, "gateway": self.gateway,
                "reason": self.reason}


def _cidr_from(ip: str, netmask: str) -> str:
    """The network CIDR for an IP + netmask (e.g. 192.168.1.37/255.255.255.0 ->
    192.168.1.0/24). Returns '' on anything unparseable — never guesses."""
    try:
        net = ipaddress.ip_network(f"{ip}/{netmask}", strict=False)
        return str(net)
    except (ValueError, ipaddress.AddressValueError):
        return ""


def _parse_lease(text: str) -> tuple:
    """Pull (ip, netmask, gateway) out of a dhclient/udhcpc/ip-addr dump. Tolerant
    of several formats; missing pieces come back empty."""
    ip = mask = gw = ""
    m = re.search(r"(?:fixed-address|ip)\s+(\d+\.\d+\.\d+\.\d+)", text)
    if not m:
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", text)
    if m:
        ip = m.group(1)
    # CIDR form "inet 192.168.1.37/24"
    cidr = re.search(r"inet\s+\d+\.\d+\.\d+\.\d+/(\d{1,2})", text)
    m2 = re.search(r"(?:subnet-mask|netmask)\s+(\d+\.\d+\.\d+\.\d+)", text)
    if m2:
        mask = m2.group(1)
    elif cidr:
        mask = str(ipaddress.ip_network(f"0.0.0.0/{cidr.group(1)}").netmask)
    g = re.search(r"(?:routers|gateway|default via)\s+(\d+\.\d+\.\d+\.\d+)", text)
    if g:
        gw = g.group(1)
    return ip, mask, gw


@dataclass
class WifiJoiner:
    """Associate to a cracked AP and take a DHCP lease on an uplink interface.

    `iface` must be an uplink/managed-capable radio, NOT the monitor capture radio
    (joining tears down monitor mode) — the Operator passes the onboard/managed one
    while AngryOxide/bettercap keep the capture radio. `runner` is injected for tests.
    """
    iface: str = "wlan0"
    runner: Callable = subprocess.run
    dhcp_client: str = "dhclient"      # or "udhcpc"
    timeout: int = 45

    def _run(self, argv, **kw):
        return self.runner(argv, capture_output=True, text=True, timeout=self.timeout,
                           check=False, **kw)

    def join(self, ssid: str, psk: str) -> JoinResult:
        if not _IFACE.fullmatch(self.iface):
            return JoinResult(False, self.iface, ssid, reason="invalid uplink interface")
        if not ssid or _SSID_BAD.search(ssid):
            return JoinResult(False, self.iface, ssid, reason="invalid/empty SSID")
        if not (8 <= len(psk) <= 63):
            return JoinResult(False, self.iface, ssid,
                              reason="PSK must be 8-63 chars (WPA2) — nothing to join with")
        # Build a wpa_supplicant config from the recovered PSK (wpa_passphrase hashes
        # it; we never echo the key into argv or logs).
        conf = self._run(["wpa_passphrase", ssid, psk])
        if conf.returncode != 0 or "psk=" not in (conf.stdout or ""):
            return JoinResult(False, self.iface, ssid,
                              reason=f"wpa_passphrase failed: {(conf.stderr or '').strip()[:80]}")
        cfg_dir = Path(tempfile.mkdtemp(prefix="redux-join-"))
        cfg = cfg_dir / "wpa.conf"
        cfg.write_text("ctrl_interface=/run/wpa_supplicant\n" + conf.stdout)
        try:
            assoc = self._run(["wpa_supplicant", "-B", "-i", self.iface, "-c", str(cfg)])
            if assoc.returncode != 0:
                return JoinResult(False, self.iface, ssid,
                                  reason=f"association failed: {(assoc.stderr or '').strip()[:80]}")
            lease = self._run([self.dhcp_client, self.iface])
            addr = self._run(["ip", "-4", "addr", "show", "dev", self.iface])
            route = self._run(["ip", "route", "show", "dev", self.iface])
            blob = "\n".join(x.stdout or "" for x in (lease, addr, route))
            ip, mask, gw = _parse_lease(blob)
            if not ip:
                return JoinResult(False, self.iface, ssid,
                                  reason="associated but no DHCP lease (no IP) — no LAN to pivot")
            subnet = _cidr_from(ip, mask) if mask else ""
            return JoinResult(True, self.iface, ssid, ip=ip, subnet=subnet, gateway=gw,
                              reason=f"joined {ssid} as {ip}" + (f" on {subnet}" if subnet else ""))
        finally:
            try:
                cfg.unlink(missing_ok=True)
                cfg_dir.rmdir()
            except OSError:
                pass
