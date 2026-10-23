"""`redux netrecon` makes the network kill-chain actually reachable and scope-gated."""
import io
import json
import contextlib

from redux.cli import main
from redux.core.scope import Scope


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = main(argv)
    return rc, out.getvalue()


def _scope_with_cidr(tmp_path, cidr="10.10.0.0/24"):
    s = Scope()
    s.arm_lab(cidrs=[cidr])
    p = tmp_path / "scope.json"
    s.save(str(p))
    return str(p)


def test_autorun_over_empty_scope_is_honest(tmp_path):
    p = tmp_path / "scope.json"
    Scope().save(str(p))
    rc, out = _run(["netrecon", "--scope-file", p.__str__(), "autorun"])
    assert rc == 0
    book = json.loads(out)
    assert "no CIDR targets in scope" in " ".join(book["notes"])


def test_autorun_sweeps_armed_cidr_and_reports_tool_absence_honestly(tmp_path):
    p = _scope_with_cidr(tmp_path)
    rc, out = _run(["netrecon", "--scope-file", p, "autorun"])
    assert rc == 0
    book = json.loads(out)
    notes = " ".join(book["notes"])
    # nmap isn't installed in the sandbox -> honest "scan unavailable", never a fake result
    assert "10.10.0.0/24" in notes and "unavailable" in notes
    assert book["credentials"] == []


def test_scan_refuses_an_unauthorized_subnet(tmp_path):
    p = _scope_with_cidr(tmp_path, "10.10.0.0/24")
    rc, out = _run(["netrecon", "--scope-file", p, "scan", "192.168.99.0/24"])
    assert rc == 3                      # not in scope
    assert "refused" in out.lower()


def test_scan_authorized_subnet_runs_and_reports_honestly(tmp_path):
    p = _scope_with_cidr(tmp_path, "10.10.0.0/24")
    rc, out = _run(["netrecon", "--scope-file", p, "scan", "10.10.0.0/24"])
    assert rc == 0
    assert "unavailable" in out.lower() or "via" in out.lower()   # nmap absent → honest
