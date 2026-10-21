#!/usr/bin/env python3
"""Find SSH-responsive devices on the laptop's local Windows LAN.

Read-only network discovery for disposable Redux hardware tests. Never logs
into another host, sends credentials, or modifies the Pi/network settings.
Limit probes to active private /24 networks to avoid broad network scans.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import socket
import subprocess
import sys


def windows_lan_addresses(*, run=subprocess.run):
    """Collect routed Windows IPv4 addresses, not the WSL2 NAT address."""
    query = (
        "Get-NetIPConfiguration | "
        "Where-Object {$_.IPv4DefaultGateway -ne $null} | "
        "ForEach-Object {$_.IPv4Address.IPAddress}"
    )
    try:
        result = run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Windows network inventory unavailable: {type(exc).__name__}") from exc
    if result.returncode:
        raise RuntimeError("Windows could not report active routed LAN addresses")
    addresses = []
    for line in result.stdout.splitlines():
        try:
            address = ipaddress.IPv4Address(line.strip())
        except ipaddress.AddressValueError:
            continue
        if address.is_private and not address.is_loopback and not address.is_link_local:
            if address not in addresses:
                addresses.append(address)
    if not addresses:
        raise RuntimeError("No private routed Windows IPv4 network detected")
    return addresses


def local_subnets(addresses, *, limit=3):
    networks = []
    for ip in addresses:
        network = ipaddress.IPv4Network(f"{ip}/24", strict=False)
        if network not in networks:
            networks.append(network)
    return networks[:limit]


def ssh_banner(address, *, dial=socket.create_connection, timeout=0.8):
    """Try one TCP handshake and SSH identification; no login attempted."""
    try:
        with dial((str(address), 22), timeout=timeout) as conn:
            conn.settimeout(timeout)
            header = conn.recv(160)
        if header.startswith(b"SSH-"):
            return str(address), header.splitlines()[0][:90].decode("ascii", "replace")
        return None
    except (OSError, TimeoutError):
        return None


def discover(networks, *, probe=ssh_banner, workers=48):
    targets = [ip for network in networks for ip in network.hosts()]
    if len(targets) > 762:
        raise ValueError("limit is three local /24 networks")
    found = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(probe, ip) for ip in targets]
        for done in as_completed(futures):
            result = done.result()
            if result:
                found.append(result)
    return sorted(found, key=lambda pair: ipaddress.IPv4Address(pair[0]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subnet", help="private /24 on your own LAN, e.g. 192.168.1.0/24")
    args = parser.parse_args(argv)
    try:
        windows_ips = windows_lan_addresses()
        networks = local_subnets(windows_ips)
        if args.subnet:
            requested = ipaddress.IPv4Network(args.subnet, strict=False)
            if requested.prefixlen != 24 or not requested.is_private:
                parser.error("only private /24 networks are supported")
            if not any(address in requested for address in windows_ips):
                parser.error("requested subnet is not connected to a Windows LAN adapter")
            networks = [requested]
        print("Scanning local Windows LAN for SSH (no login attempted):")
        print("  " + ", ".join(str(network) for network in networks), flush=True)
        found = discover(networks)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not found:
        print("No SSH servers discovered. Check that Pi Ethernet is connected to the router.")
        print("If the Pi has another subnet, get its IP from the router's connected-device list.")
        return 1
    print("\nSSH-responsive addresses:")
    for address, banner in found:
        print(f"  {address:15} {banner}")
    print("\nTry a likely address from Ubuntu:")
    if len(found) == 1:
        print(f"  ssh -tt -i ~/.ssh/redux_field_ed25519 pi@{found[0][0]} 'sudo redux-report --full'")
    else:
        print("  ssh -tt -i ~/.ssh/redux_field_ed25519 pi@<ADDRESS> 'sudo redux-report --full'")
    print("These are SSH services, not confirmed Redux identities.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
