"""Offline tests for the opt-in two-host LAN fingerprint tool."""
import importlib.util
import ipaddress
from pathlib import Path
import subprocess

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/identify-lan-hosts.py"
SPEC = importlib.util.spec_from_file_location("identify_lan_hosts", SOURCE)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_explicit_private_targets_only():
    assert list(map(str, mod.parse_targets([]))) == [
        "192.168.12.117", "192.168.12.206"]
    assert list(map(str, mod.parse_targets(["10.1.1.2", "10.1.1.2"]))) == ["10.1.1.2"]
    for target in ("8.8.8.8", "127.0.0.1", "169.254.1.1", "::1", "not-an-ip"):
        with pytest.raises(SystemExit):
            mod.parse_targets([target])
    with pytest.raises(SystemExit):
        mod.parse_targets(["10.1.1.1"] * 5)


def test_windows_neighbor_query_has_only_requested_ips():
    def runner(argv, **kwargs):
        assert argv[:3] == ["powershell.exe", "-NoProfile", "-NonInteractive"]
        ps = argv[-1]
        assert "'192.168.12.117'" in ps
        assert "'192.168.12.206'" in ps
        assert "Get-NetNeighbor" in ps and "Resolve-DnsName" in ps
        assert "ping.exe -n 1 -w 400" in ps
        return subprocess.CompletedProcess(argv, 0,
            '[{"ip":"192.168.12.117","mac":"AA-BB-CC-DD-EE-FF",'
            '"neighbor":"Reachable","ptr":"labbox.lan"},'
            '{"ip":"192.168.12.206","mac":null,"neighbor":null,"ptr":null}]', "")
    rows, error = mod.windows_identity(mod.parse_targets([]), runner=runner)
    assert error == ""
    assert rows["192.168.12.117"]["ptr"] == "labbox.lan"
    assert rows["192.168.12.206"]["mac"] is None


def test_windows_inventory_failure_keeps_identity_unknown():
    ips = mod.parse_targets([])
    def runner(argv, **kwargs):
        raise FileNotFoundError()
    assert mod.windows_identity(ips, runner=runner)[0] == {}


def test_port_scan_limits_to_explicit_ports():
    calls = []
    def probe(ip, port):
        calls.append((str(ip), port))
        return port if str(ip) == "192.168.12.206" and port in (22, 80) else None
    result = mod.port_inventory(mod.parse_targets([]), probe=probe)
    assert result["192.168.12.117"] == []
    assert result["192.168.12.206"] == [22, 80]
    assert len(calls) == 2 * len(mod.PORTS)
    assert all(port in mod.PORTS for _, port in calls)


def test_netbios_name_is_optional_readonly():
    def runner(argv, **kwargs):
        assert argv == ["nbtstat.exe", "-A", "192.168.12.206"]
        return subprocess.CompletedProcess(argv, 0,
            "      NASDEVICE      <00>  UNIQUE Registered\n", "")
    assert mod.netbios_name(ipaddress.IPv4Address("192.168.12.206"),
                            runner=runner) == "NASDEVICE"


def test_report_can_identify_service_but_does_not_claim_hardware(monkeypatch, capsys):
    monkeypatch.setattr(mod, "windows_identity", lambda ips: ({
        "192.168.12.117": {"mac": "AA-BB-CC-DD-EE-FF",
                              "ptr": "linuxbox.lan", "neighbor": "Reachable"}}, ""))
    monkeypatch.setattr(mod, "netbios_name", lambda ip: "")
    monkeypatch.setattr(mod, "port_inventory", lambda ips: {
        "192.168.12.117": [22, 80], "192.168.12.206": [22]})
    monkeypatch.setattr(mod, "ssh_banner", lambda ip: "SSH-2.0-OpenSSH")
    monkeypatch.setattr(mod, "http_identity", lambda ip, port: {
        "port": port, "status": 200, "server": "Example server",
        "title": "Local Gateway", "redirect": ""})
    assert mod.main([]) == 0
    out = capsys.readouterr().out
    assert "linuxbox.lan" in out
    assert "Local Gateway" in out
    assert "SSH-2.0-OpenSSH" in out
    assert "not verified hardware ownership or model" in out
