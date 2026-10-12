"""Local network finder tests: no actual network scans or Windows tools."""
from __future__ import annotations

import importlib.util
import ipaddress
from pathlib import Path
import subprocess

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/find-redux-lan.py"
SPEC = importlib.util.spec_from_file_location("redux_lan_finder", SOURCE)
finder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finder)


def test_windows_inventory_uses_routed_ip_not_wsl_virtual_interface():
    def runner(argv, **kwargs):
        assert argv[0] == "powershell.exe"
        assert "Get-NetIPConfiguration" in argv[-1]
        return subprocess.CompletedProcess(argv, 0,
            "192.168.50.15\n10.0.0.33\n127.0.0.1\n192.168.50.15\n", "")
    found = finder.windows_lan_addresses(run=runner)
    assert [str(x) for x in found] == ["192.168.50.15", "10.0.0.33"]
    assert [str(x) for x in finder.local_subnets(found)] == [
        "192.168.50.0/24", "10.0.0.0/24",
    ]


def test_probe_reads_only_ssh_banner_and_never_authenticates():
    requests = []
    class FakeConnection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def settimeout(self, timeout): requests.append(("timeout", timeout))
        def recv(self, length):
            requests.append(("read", length))
            return b"SSH-2.0-OpenSSH_9.6\r\n"
    def dial(address, timeout):
        requests.append(("connect", address, timeout))
        return FakeConnection()
    target = ipaddress.IPv4Address("192.168.50.20")
    assert finder.ssh_banner(target, dial=dial) == (
        "192.168.50.20", "SSH-2.0-OpenSSH_9.6")
    assert requests[0][0] == "connect"
    assert not any("login" in repr(request) for request in requests)


def test_discovery_is_bounded_to_a_local_subnet():
    network = ipaddress.IPv4Network("192.168.77.0/24")
    observed = []
    def fake_probe(address):
        observed.append(address)
        if str(address) == "192.168.77.42":
            return str(address), "SSH-2.0-OpenSSH"
        return None
    found = finder.discover([network], probe=fake_probe, workers=8)
    assert found == [("192.168.77.42", "SSH-2.0-OpenSSH")]
    assert len(observed) == 254


def test_excessively_broad_network_scans_are_rejected():
    nets = [ipaddress.IPv4Network(f"10.9.{i}.0/24") for i in range(4)]
    with pytest.raises(ValueError, match="limit"):
        finder.discover(nets, probe=lambda address: None)


def test_report_no_ssh_does_not_invent_host_identity(monkeypatch, capsys):
    monkeypatch.setattr(finder, "windows_lan_addresses",
                        lambda: [ipaddress.IPv4Address("192.168.1.8")])
    monkeypatch.setattr(finder, "discover", lambda networks: [])
    assert finder.main([]) == 1
    assert "No SSH servers discovered" in capsys.readouterr().out
