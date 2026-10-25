"""Loot vault + scope-gated network pivot (FIELD plans, LAB fires)."""
import json
import os
import stat
from types import SimpleNamespace

from redux.loot import LootVault, LootEntry, pivot
from redux.core.scope import Scope
from redux.netrecon.pipeline import ReplayScanner, ReplayCredTester, Host, Port


# --- vault ------------------------------------------------------------------ #

def test_vault_add_find_and_report_keys(tmp_path):
    v = LootVault(str(tmp_path / "v.json"))
    v.add(LootEntry(kind="wifi-psk", target="aa:bb:cc:dd:ee:ff", secret="hunter2hunter2",
                    ssid="HomeNet", source="crack"), logger=None)
    assert v.find_psk(bssid="aa:bb:cc:dd:ee:ff") == "hunter2hunter2"
    assert v.find_psk(ssid="HomeNet") == "hunter2hunter2"
    d = v.entries()[0].to_dict()
    assert d["bssid"] == "aa:bb:cc:dd:ee:ff" and d["psk"] == "hunter2hunter2"   # report-friendly


def test_vault_dedupes_and_updates(tmp_path):
    v = LootVault(str(tmp_path / "v.json"))
    assert v.add(LootEntry("wifi-psk", "aa:bb:cc:dd:ee:ff", "old"), logger=None) is True
    assert v.add(LootEntry("wifi-psk", "aa:bb:cc:dd:ee:ff", "new"), logger=None) is False  # update
    assert len(v) == 1 and v.find_psk(bssid="aa:bb:cc:dd:ee:ff") == "new"


def test_vault_log_detail_never_contains_the_secret(tmp_path):
    logged = []
    v = LootVault(str(tmp_path / "v.json"))
    v.add(LootEntry("wifi-psk", "aa:bb:cc:dd:ee:ff", "topsecret", ssid="Net", source="crack"),
          logger=lambda event, detail: logged.append((event, detail)))
    assert logged and logged[0][0] == "loot.add"
    blob = json.dumps(logged[0][1])
    assert "topsecret" not in blob and "secret" not in logged[0][1] and "psk" not in logged[0][1]
    assert logged[0][1]["target"] == "aa:bb:cc:dd:ee:ff"


def test_vault_persists_owner_only(tmp_path):
    p = tmp_path / "v.json"
    LootVault(str(p)).add(LootEntry("wifi-psk", "aa:bb", "s3cret"), logger=None)
    assert LootVault(str(p)).find_psk(bssid="aa:bb") == "s3cret"      # survives reload
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600                 # owner-only


# --- pivot gating (the core of the mode contract) --------------------------- #

def _recon():
    scanner = ReplayScanner({"10.0.0.0/24": [Host(ip="10.0.0.5", ports=(Port(22, "tcp", "ssh"),))]})
    tester = ReplayCredTester({("10.0.0.5", 22): ("admin", "hunter2")})
    return scanner, tester


def test_field_mode_plans_but_does_not_fire_without_authorization(tmp_path):
    scope = Scope(); scope.add("10.0.0.0/24", "cidr", job="job")       # armed, but FIELD
    scanner, tester = _recon()
    res = pivot(scope, authorized=False, subnet="10.0.0.0/24",
                usernames=["admin"], passwords=["hunter2"], scanner=scanner, tester=tester,
                vault=LootVault(str(tmp_path / "v.json")), logger=None)
    assert res.executed is False and res.mode == "field"
    assert "plan only" in res.reason and res.planned_steps
    assert res.hosts == 0 and res.creds_found == 0                    # nothing fired


def test_field_mode_fires_when_authorized_and_in_scope(tmp_path):
    scope = Scope(); scope.add("10.0.0.0/24", "cidr", job="job")
    scanner, tester = _recon()
    logged = []
    v = LootVault(str(tmp_path / "v.json"))
    res = pivot(scope, authorized=True, subnet="10.0.0.0/24",
                usernames=["admin"], passwords=["hunter2"], scanner=scanner, tester=tester,
                vault=v, logger=lambda e, d: logged.append((e, d)))
    assert res.executed and res.hosts == 1 and res.creds_found == 1 and res.vault_added == 1
    assert v.entries()[0].kind == "service-cred" and v.entries()[0].secret == "hunter2"
    assert any(e == "pivot.run" for e, _ in logged)


def test_lab_mode_fires_on_sight_for_private_subnet(tmp_path):
    scope = Scope(lab_mode=True)                                      # nothing armed
    scanner, tester = _recon()
    res = pivot(scope, authorized=False, subnet="10.0.0.0/24",        # private, approved on sight
                usernames=["admin"], passwords=["hunter2"], scanner=scanner, tester=tester,
                vault=LootVault(str(tmp_path / "v.json")), logger=None)
    assert res.executed and res.mode == "lab" and res.creds_found == 1


def test_field_authorized_but_out_of_scope_is_refused(tmp_path):
    scope = Scope(); scope.add("192.168.1.0/24", "cidr", job="job")   # a DIFFERENT subnet armed
    scanner, tester = _recon()
    res = pivot(scope, authorized=True, subnet="10.0.0.0/24",         # not armed
                usernames=["admin"], passwords=["hunter2"], scanner=scanner, tester=tester,
                vault=LootVault(str(tmp_path / "v.json")), logger=None)
    assert res.executed and res.hosts == 0                           # passed the gate, but recon refused
    assert any("refused" in n for n in res.notes)


def test_lab_never_auto_approves_a_public_range(tmp_path):
    scope = Scope(lab_mode=True)
    scanner, tester = _recon()
    res = pivot(scope, authorized=False, subnet="8.8.8.0/24",         # public — never on sight
                usernames=["admin"], passwords=["hunter2"], scanner=scanner, tester=tester,
                vault=LootVault(str(tmp_path / "v.json")), logger=None)
    assert res.executed and res.hosts == 0
    assert any("refused" in n or "public" in n for n in res.notes)


# --- CLI -------------------------------------------------------------------- #

def _ns(**kw):
    base = dict(vault=None, loot_cmd=None, psk=None, bssid=None, ssid=None, source="manual",
                reveal=False, json=False, subnet=None, user=None, pw=None, authorized=False,
                scope_file=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_cmd_loot_add_list_redacts(tmp_path, capsys, monkeypatch):
    from redux.cli import cmd_loot
    monkeypatch.setenv("REDUX_AUTHLOG", str(tmp_path / "a.jsonl"))
    vp = str(tmp_path / "v.json")
    assert cmd_loot(_ns(vault=vp, loot_cmd="add", psk="hunter2hunter2",
                        bssid="aa:bb:cc:dd:ee:ff", ssid="HomeNet", source="crack")) == 0
    capsys.readouterr()
    assert cmd_loot(_ns(vault=vp, loot_cmd="list")) == 0
    out = capsys.readouterr().out
    assert "HomeNet" in out or "aa:bb:cc:dd:ee:ff" in out
    assert "hunter2hunter2" not in out and "redacted" in out
    assert cmd_loot(_ns(vault=vp, loot_cmd="list", reveal=True)) == 0
    assert "hunter2hunter2" in capsys.readouterr().out


def test_cmd_loot_pivot_field_plans(tmp_path, capsys):
    from redux.cli import cmd_loot
    scope_file = tmp_path / "scope.json"
    s = Scope(path=str(scope_file)); s.add("10.0.0.0/24", "cidr", job="job"); s.save()
    assert cmd_loot(_ns(vault=str(tmp_path / "v.json"), loot_cmd="pivot", subnet="10.0.0.0/24",
                        scope_file=str(scope_file), authorized=False)) == 0
    out = capsys.readouterr().out
    assert "NOT fired" in out and "FIELD" in out and "plan:" in out
