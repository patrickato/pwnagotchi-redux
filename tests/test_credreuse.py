"""Credential-reuse graph — one password's blast radius across hosts."""
from redux.netrecon.credreuse import (
    CredReuse, CredPair, Target, ReuseResult, creds_from_vault,
)


def _targets():
    return [Target("10.0.0.5", 22, "ssh"), Target("10.0.0.6", 445, "smb"),
            Target("10.0.0.7", 22, "ssh")]


def test_plan_is_cartesian_minus_origin():
    creds = [CredPair("root", "toor", origin="10.0.0.5")]
    attempts = CredReuse().plan(creds, _targets())
    hosts = {a.target.host for a in attempts}
    assert "10.0.0.5" not in hosts           # it already worked there — not reuse
    assert hosts == {"10.0.0.6", "10.0.0.7"}


def test_plan_prioritizes_admin_usernames():
    creds = [CredPair("bob", "pw"), CredPair("root", "toor")]
    attempts = CredReuse().plan(creds, [Target("10.0.0.6", 22, "ssh")])
    assert attempts[0].cred.username == "root"   # admin tried first


def test_plan_scope_gates_targets():
    class _Scope:
        def authorize(self, *, ip=None, now=None):
            return (ip == "10.0.0.6", "armed" if ip == "10.0.0.6" else "not in scope")
    attempts = CredReuse(scope=_Scope()).plan([CredPair("root", "toor")], _targets())
    assert {a.target.host for a in attempts} == {"10.0.0.6"}


def test_spray_refuses_in_field_without_authorization():
    r = CredReuse().spray([CredPair("root", "toor")], _targets(), tester=lambda *a: True)
    assert r == []                            # FIELD + not authorized -> nothing


def test_spray_runs_in_lab_and_records_results():
    # root:toor works on .6 and .7, not elsewhere
    def tester(host, port, service, user, secret):
        return user == "root" and host in ("10.0.0.6", "10.0.0.7")
    creds = [CredPair("root", "toor", origin="10.0.0.5")]
    results = CredReuse().spray(creds, _targets(), tester=tester, lab=True)
    worked = {r.target.host for r in results if r.worked}
    assert worked == {"10.0.0.6", "10.0.0.7"}


def test_graph_shows_blast_radius_and_exposure():
    c = CredPair("root", "toor")
    results = [
        ReuseResult(c, Target("10.0.0.6", 445, "smb"), True, "reused"),
        ReuseResult(c, Target("10.0.0.7", 22, "ssh"), True, "reused"),
        ReuseResult(CredPair("svc", "x"), Target("10.0.0.6", 3306, "sql"), True, "reused"),
        ReuseResult(CredPair("svc", "x"), Target("10.0.0.8", 22, "ssh"), False, "rejected"),
    ]
    g = CredReuse().graph(results)
    assert g["credentials_that_reuse"] == 2 and g["hosts_unlocked"] == 2
    assert g["most_valuable_credential"]["unlocks"] == ["10.0.0.6:445", "10.0.0.7:22"]
    assert g["most_valuable_credential"]["credential"] == "root:***"   # secret redacted
    assert g["most_exposed_host"]["host"] == "10.0.0.6"                # 2 creds open it
    assert "lateral movement" in g["note"]


def test_graph_empty_is_honest():
    g = CredReuse().graph([ReuseResult(CredPair("a", "b"), Target("h"), False)])
    assert g["credentials_that_reuse"] == 0 and "no credential reuse" in g["note"]


def test_creds_from_vault_extracts_service_creds():
    class _E:
        def __init__(self, u, s, t):
            self.username, self.secret, self.target = u, s, t
    creds = creds_from_vault([_E("root", "toor", "10.0.0.5"), _E("", "psk-only", "ap"),
                              _E("root", "toor", "10.0.0.5")])   # dupe
    assert len(creds) == 1 and creds[0].origin == "10.0.0.5"     # PSK-only skipped, deduped
