"""Network-recon axis — scope-gated scan / cred-test / autonomous loot sweep."""
import pytest

from redux.netrecon import (
    Host, Port, ReplayScanner, ReplayCredTester, NmapScanner, HydraCredTester,
    recon_subnet, try_credentials, autorun, ReconRefused,
)
from redux.core import Scope


def _scope_with(cidr="192.168.50.0/24", job="acme"):
    s = Scope(); s.add(cidr, job=job)
    return s


def _hosts():
    return [Host(ip="192.168.50.10", hostname="nas",
                 ports=(Port(22, "tcp", "ssh"), Port(445, "tcp", "smb")))]


def test_recon_refuses_unscoped_subnet():
    s = Scope()  # empty
    with pytest.raises(ReconRefused):
        recon_subnet("192.168.50.0/24", scope=s, scanner=ReplayScanner())


def test_recon_scans_authorized_subnet():
    s = _scope_with()
    scanner = ReplayScanner({"192.168.50.0/24": _hosts()})
    res = recon_subnet("192.168.50.0/24", scope=s, scanner=scanner)
    assert res.available and len(res.hosts) == 1 and res.hosts[0].ip == "192.168.50.10"


def test_test_service_refuses_unscoped_host():
    s = _scope_with("10.0.0.0/24")                       # different subnet armed
    with pytest.raises(ReconRefused):
        try_credentials("192.168.50.10", 22, "ssh", ["root"], ["toor"],
                     scope=s, tester=ReplayCredTester())


def test_test_service_finds_authorized_credential():
    s = _scope_with()
    tester = ReplayCredTester(found={("192.168.50.10", 22): ("admin", "hunter2")})
    r = try_credentials("192.168.50.10", 22, "ssh", ["admin"], ["hunter2"], scope=s, tester=tester)
    assert r.found is not None and r.found.username == "admin" and r.found.secret == "hunter2"


def test_autorun_sweeps_only_the_armed_scope():
    s = _scope_with()
    scanner = ReplayScanner({"192.168.50.0/24": _hosts()})
    tester = ReplayCredTester(found={("192.168.50.10", 22): ("admin", "hunter2")})
    book = autorun(scope=s, scanner=scanner, tester=tester,
                   usernames=["admin"], passwords=["hunter2"])
    assert len(book.hosts) == 1
    assert len(book.credentials) == 1 and book.credentials[0].service == "ssh"
    d = book.to_dict()
    assert d["summary"] == "1 host(s), 1 credential(s)"


def test_autorun_empty_scope_sweeps_nothing():
    book = autorun(scope=Scope(), scanner=ReplayScanner())
    assert book.hosts == [] and any("arm a subnet" in n for n in book.notes)


def test_autorun_service_filter_skips_out_of_class_ports():
    s = _scope_with()
    hosts = [Host(ip="192.168.50.11", ports=(Port(9999, "tcp", "weird"),))]
    scanner = ReplayScanner({"192.168.50.0/24": hosts})
    tester = ReplayCredTester(found={("192.168.50.11", 9999): ("a", "b")})
    book = autorun(scope=s, scanner=scanner, tester=tester,
                   usernames=["a"], passwords=["b"], services=("ssh", "ftp"))
    assert book.credentials == []           # 'weird' isn't in the service class list


def test_nmap_and_hydra_absent_are_honest(monkeypatch):
    monkeypatch.setattr("redux.netrecon.pipeline.shutil.which", lambda _b: None)
    s = _scope_with()
    res = recon_subnet("192.168.50.0/24", scope=s, scanner=NmapScanner())
    assert res.available is False and "not installed" in res.reason
    cr = try_credentials("192.168.50.10", 22, "ssh", ["x"], ["y"], scope=s, tester=HydraCredTester())
    assert cr.available is False and "not installed" in cr.reason


# --- live nmap/hydra execution: real argv + parse, no network --------------- #

_NMAP_XML = """<?xml version="1.0"?><nmaprun>
  <host><status state="up"/><address addr="192.168.50.10" addrtype="ipv4"/>
    <hostnames><hostname name="nas.local" type="PTR"/></hostnames>
    <ports>
      <port protocol="tcp" portid="22"><state state="open"/>
        <service name="ssh" product="OpenSSH"/></port>
      <port protocol="tcp" portid="443"><state state="filtered"/></port>
      <port protocol="tcp" portid="445"><state state="open"/>
        <service name="microsoft-ds"/></port>
    </ports></host>
  <host><status state="down"/><address addr="192.168.50.99" addrtype="ipv4"/></host>
</nmaprun>"""


def _runner(stdout):
    from types import SimpleNamespace
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)
    run.calls = calls
    return run


def test_nmap_runs_real_argv_and_parses_open_ports(monkeypatch):
    monkeypatch.setattr("redux.netrecon.pipeline.shutil.which", lambda _b: "/usr/bin/nmap")
    run = _runner(_NMAP_XML)
    s = _scope_with()
    res = recon_subnet("192.168.50.0/24", scope=s,
                       scanner=NmapScanner(runner=run))
    assert res.available is True and res.tool == "nmap"
    assert "-sV" in run.calls[0] and "-oX" in run.calls[0] and "192.168.50.0/24" in run.calls[0]
    assert len(res.hosts) == 1                       # the 'down' host is dropped
    h = res.hosts[0]
    assert h.ip == "192.168.50.10" and h.hostname == "nas.local"
    assert {p.number for p in h.ports} == {22, 445}  # 'filtered' 443 excluded
    assert next(p for p in h.ports if p.number == 22).service == "ssh"


def test_nmap_rejects_bad_target_and_handles_timeout(monkeypatch):
    monkeypatch.setattr("redux.netrecon.pipeline.shutil.which", lambda _b: "/usr/bin/nmap")
    bad = NmapScanner(runner=_runner(_NMAP_XML)).scan("not-a-cidr; rm -rf /")
    assert bad.available is False and "valid" in bad.reason.lower()
    def boom(argv, **kw):
        raise __import__("subprocess").TimeoutExpired(argv, 1)
    t = NmapScanner(runner=boom).scan("192.168.50.0/24")
    assert t.available is False and "timed out" in t.reason


def test_hydra_parses_hit_and_builds_real_argv(monkeypatch):
    monkeypatch.setattr("redux.netrecon.pipeline.shutil.which", lambda _b: "/usr/bin/hydra")
    out = ("Hydra v9.1\n[DATA] attacking ssh://192.168.50.10:22/\n"
           "[22][ssh] host: 192.168.50.10   login: root   password: toor\n"
           "1 valid password found\n")
    run = _runner(out)
    s = _scope_with()
    cr = try_credentials("192.168.50.10", 22, "ssh", ["root"], ["toor"],
                         scope=s, tester=HydraCredTester(runner=run))
    assert cr.available is True and cr.found is not None
    assert cr.found.username == "root" and cr.found.secret == "toor"
    assert "ssh://192.168.50.10" in run.calls[0] and "-f" in run.calls[0]


def test_hydra_clean_run_no_hit_is_not_a_fabrication(monkeypatch):
    monkeypatch.setattr("redux.netrecon.pipeline.shutil.which", lambda _b: "/usr/bin/hydra")
    run = _runner("Hydra v9.1\n0 valid passwords found\n")
    cr = HydraCredTester(runner=run).test("192.168.50.10", 22, "ssh", ["root"], ["x"])
    assert cr.available is True and cr.found is None


def test_hydra_rejects_unknown_service_and_bad_host(monkeypatch):
    monkeypatch.setattr("redux.netrecon.pipeline.shutil.which", lambda _b: "/usr/bin/hydra")
    bad_svc = HydraCredTester().test("192.168.50.10", 22, "weird-proto", ["a"], ["b"])
    assert bad_svc.available is False and "service" in bad_svc.reason
    bad_ip = HydraCredTester().test("not-an-ip", 22, "ssh", ["a"], ["b"])
    assert bad_ip.available is False and "host" in bad_ip.reason.lower()
