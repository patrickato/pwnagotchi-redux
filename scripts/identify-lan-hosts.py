#!/usr/bin/env python3
"""Identify two specified devices on the owner's local network.

No credentials, login attempts, port-range scan, or configuration changes.
Combines Windows ARP/reverse-DNS/NetBIOS with a few bounded TCP/HTTP probes.
These are fingerprints, not proofs of physical identity.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import http.client
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import sys
from urllib.parse import urlsplit

PORTS = (21, 22, 23, 53, 80, 81, 139, 443, 445, 5000, 5900,
         8000, 8008, 8080, 8443, 8888, 9100)
HTTP_PORTS = (80, 81, 443, 5000, 8000, 8008, 8080, 8443, 8888)
TITLES = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)


def safe(value, limit=140):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))[:limit].strip()


def parse_targets(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ips", nargs="*", default=["192.168.12.117", "192.168.12.206"],
                        help="specific private IPv4 devices to fingerprint")
    args = parser.parse_args(argv)
    if not 1 <= len(args.ips) <= 4:
        parser.error("choose one to four specific local IP addresses")
    ips = []
    for raw in args.ips:
        try:
            ip = ipaddress.IPv4Address(raw)
        except ipaddress.AddressValueError:
            parser.error(f"not a valid IPv4 address: {raw}")
        if not ip.is_private or ip.is_loopback or ip.is_link_local:
            parser.error("only private LAN IPv4 addresses are allowed")
        if ip not in ips:
            ips.append(ip)
    return ips


def windows_identity(ips, *, runner=subprocess.run):
    """Ask Windows for its adjacent-LAN MAC/hostname evidence.

    Windows is used because WSL NAT neighbors are not Windows LAN ARP
    neighbors. A single ICMP echo per target may populate Windows ARP.
    """
    quoted = ",".join("'" + str(ip) + "'" for ip in ips)
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$found=@();"
        f"foreach($ip in @({quoted})){{"
        "ping.exe -n 1 -w 400 $ip | Out-Null;"
        "$n=Get-NetNeighbor -AddressFamily IPv4 -IPAddress $ip | "
        "Where-Object { $_.LinkLayerAddress -and "
        "$_.LinkLayerAddress -ne '00-00-00-00-00-00' } | Select-Object -First 1;"
        "$d=Resolve-DnsName -Name $ip -Type PTR -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Type -eq 'PTR' } | Select-Object -First 1;"
        "$found+= [pscustomobject]@{ip=$ip; mac=$n.LinkLayerAddress; "
        "neighbor=$n.State.ToString(); ptr=$d.NameHost};"
        "};"
        "ConvertTo-Json -InputObject $found -Compress -Depth 3"
    )
    try:
        proc = runner(["powershell.exe", "-NoProfile", "-NonInteractive",
                       "-Command", ps], capture_output=True, text=True,
                      timeout=25, check=False)
        if proc.returncode:
            return {}, "Windows neighbor lookup unavailable"
        records = json.loads(proc.stdout)
        return {x["ip"]: x for x in records if isinstance(x, dict)}, ""
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}, "Windows neighbor lookup unavailable"


def netbios_name(ip, *, runner=subprocess.run):
    """Single optional NBNS name query, not an authenticated connection."""
    try:
        proc = runner(["nbtstat.exe", "-A", str(ip)], capture_output=True,
                      text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for line in proc.stdout.splitlines():
        if "<00>" in line and ("UNIQUE" in line.upper() or "REGISTERED" in line.upper()):
            return safe(line.split("<00>")[0], 45)
    return ""


def open_port(ip, port, *, dial=socket.create_connection):
    try:
        with dial((str(ip), port), timeout=0.65):
            return port
    except (OSError, TimeoutError):
        return None


def port_inventory(ips, *, probe=open_port):
    results = {str(ip): [] for ip in ips}
    with ThreadPoolExecutor(max_workers=40) as pool:
        futures = {pool.submit(probe, ip, port): str(ip)
                   for ip in ips for port in PORTS}
        for task in as_completed(futures):
            result = task.result()
            if result is not None:
                results[futures[task]].append(result)
    for key in results:
        results[key].sort()
    return results


def ssh_banner(ip, *, dial=socket.create_connection):
    try:
        with dial((str(ip), 22), timeout=1) as sock:
            sock.settimeout(1)
            banner = sock.recv(120)
        if banner.startswith(b"SSH-"):
            return safe(banner.splitlines()[0].decode("ascii", "replace"), 110)
    except (OSError, TimeoutError):
        pass
    return ""


def http_identity(ip, port):
    """GET / only; no authentication, cookies, redirects, or additional pages."""
    secure = port in (443, 8443)
    conn = None
    try:
        if secure:
            conn = http.client.HTTPSConnection(
                str(ip), port, timeout=2, context=ssl._create_unverified_context())
        else:
            conn = http.client.HTTPConnection(str(ip), port, timeout=2)
        conn.request("GET", "/", headers={"User-Agent": "Redux-Field-Inventory/1.0",
                                          "Accept": "text/html"})
        res = conn.getresponse()
        body = res.read(16384).decode("utf-8", "replace")
        title = TITLES.search(body)
        location = res.getheader("Location", "")
        redirect = ""
        if location:
            # Path only, with no query tokens or login credentials.
            loc = urlsplit(location)
            redirect = safe(loc.path, 60)
            if loc.hostname:
                redirect = safe(loc.hostname, 80) + redirect
        return {
            "port": port, "status": res.status,
            "server": safe(res.getheader("Server", ""), 90),
            "title": safe(html.unescape(title.group(1)) if title else "", 100),
            "redirect": redirect,
        }
    except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError,
            ValueError, UnicodeError):
        return {}
    finally:
        if conn is not None:
            conn.close()


def examine(ips):
    neighbors, warning = windows_identity(ips)
    ports = port_inventory(ips)
    for ip in ips:
        key = str(ip)
        print(f"\n{'=' * 17} {key} {'=' * 17}")
        row = neighbors.get(key, {})
        print("Windows MAC:   " + safe(row.get("mac") or "(not resolved)"))
        print("Neighbor:      " + safe(row.get("neighbor") or "(not resolved)"))
        print("Reverse DNS:   " + safe(row.get("ptr") or "(none)"))
        nb = netbios_name(ip)
        print("NetBIOS name:  " + (nb or "(none)"))
        print("TCP open:      " + (", ".join(map(str, ports[key])) or "(none detected)"))
        if 22 in ports[key]:
            print("SSH banner:    " + (ssh_banner(ip) or "(not readable)"))
        for p in ports[key]:
            if p in HTTP_PORTS:
                result = http_identity(ip, p)
                if result:
                    parts = [f"{'HTTPS' if p in (443,8443) else 'HTTP'}:{p}",
                             str(result["status"])]
                    for k in ("server", "title", "redirect"):
                        if result[k]:
                            parts.append(f"{k}={result[k]}")
                    print("Web identity:  " + " | ".join(parts))
    if warning:
        print("\nNote: " + warning)
    print("\nThis is a service fingerprint, not verified hardware ownership or model.")
    print("To confirm exact device names/models, match the MACs to your router's")
    print("connected devices/DHCP list, or inspect a device you own directly.")
    return 0


def main(argv=None):
    return examine(parse_targets(argv))


if __name__ == "__main__":
    raise SystemExit(main())
