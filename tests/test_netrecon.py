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
