"""Engagement-report additions: auth-log timeline bridge, integrity proof,
remediation from the purple-team KB, and HTML rendering."""
from types import SimpleNamespace

from redux.report import (build_report, render_markdown, render_html,
                          actions_from_authlog, EngagementAction)
from redux.core.scope import Scope
from redux.core.authlog import AuthLog


def _scope():
    s = Scope()
    s.add("00:11:22:33:44:55", "bssid", job="lab")
    return s


# --- auth-log bridge -------------------------------------------------------- #

def test_actions_from_authlog_targets_only(tmp_path):
    log = AuthLog(str(tmp_path / "a.jsonl"))
    log.record("scope.add", {"value": "00:11:22:33:44:55", "kind": "bssid", "job": "lab"})
    log.record("scope.lab_mode", {"state": "on"})           # no target -> skipped
    log.record("fire.deauth", {"bssid": "00:11:22:33:44:55", "reason": "forced reassoc"})
    acts = actions_from_authlog(log.entries())
    assert [a.action for a in acts] == ["scope.add", "fire.deauth"]   # lab_mode dropped
    assert acts[0].target == "00:11:22:33:44:55"
    assert acts[1].target == "00:11:22:33:44:55"


# --- integrity proof + remediation in the report --------------------------- #

def test_report_carries_authlog_integrity_and_remediation():
    acts = [EngagementAction(1.0, "handshake_capture", "00:11:22:33:44:55", "cap", "got it")]
    rep = build_report("eng", "op", _scope(), acts, now=10.0,
                       authlog_integrity={"ok": True, "reason": "intact", "signed": True, "count": 3})
    assert rep["authlog"]["ok"] and rep["authlog"]["signed"]
    kinds = {r["technique"] for r in rep["remediation"]}
    assert any("handshake" in k.lower() for k in kinds)      # handshake_capture mapped
    assert any("WPA3" in m for r in rep["remediation"] for m in r["mitigations"])


def test_remediation_empty_when_no_actionable_techniques():
    acts = [EngagementAction(1.0, "wifi_recon", "00:11:22:33:44:55")]   # passive, no fix
    rep = build_report("eng", "op", _scope(), acts, now=10.0)
    assert rep["remediation"] == []


def test_markdown_shows_authlog_and_remediation_sections():
    acts = [EngagementAction(1.0, "deauth", "00:11:22:33:44:55")]
    md = render_markdown(build_report("eng", "op", _scope(), acts, now=10.0,
                         authlog_integrity={"ok": True, "reason": "intact — 1 entry", "signed": True, "count": 1}))
    assert "Chain-of-authorization log (tamper-evidence)" in md
    assert "cryptographically signed" in md
    assert "## Remediation" in md and ("PMF" in md or "802.11w" in md)


def test_markdown_flags_broken_authlog():
    md = render_markdown(build_report("eng", "op", _scope(), [], now=10.0,
                         authlog_integrity={"ok": False, "reason": "entry 2: hash mismatch",
                                            "signed": True, "count": 2}))
    assert "BROKEN — do not trust this log" in md


# --- HTML rendering --------------------------------------------------------- #

def test_render_html_is_dark_and_escapes():
    acts = [EngagementAction(1.0, "deauth", "<b>evil</b>", "x")]
    htm = render_html(build_report("eng", "op", _scope(), acts, now=10.0))
    assert htm.startswith("<!doctype html>") and "#06090d" in htm
    assert "<b>evil</b>" not in htm and "&lt;b&gt;evil" in htm
    assert "UNAUTHORIZED" in htm                             # the <b>evil</b> target isn't armed


# --- CLI: report build --from-authlog -------------------------------------- #

def test_cmd_report_build_from_authlog(tmp_path, capsys):
    from redux.cli import cmd_report
    scope_file = tmp_path / "scope.json"
    s = Scope(path=str(scope_file)); s.add("00:11:22:33:44:55", "bssid", job="lab"); s.save()
    authlog = tmp_path / "a.jsonl"
    AuthLog(str(authlog)).record("scope.add", {"value": "00:11:22:33:44:55", "kind": "bssid"})
    out_md = tmp_path / "r.md"; out_html = tmp_path / "r.html"
    args = SimpleNamespace(report_cmd="build", operator="patrick", sanitize=False,
                           out=str(out_md), html=str(out_html), engagement="Lab Run",
                           scope_file=str(scope_file), actions=None, from_authlog=True,
                           authlog=str(authlog), key=str(tmp_path / "nokey"))
    assert cmd_report(args) == 0
    md = out_md.read_text()
    assert "Lab Run" in md and "Chain-of-authorization log" in md
    assert "scope.add" in md                                 # timeline came from the log
    assert out_html.read_text().startswith("<!doctype html>")
    assert "wrote" in capsys.readouterr().out


def test_cmd_report_build_requires_a_source(tmp_path, capsys):
    from redux.cli import cmd_report
    scope_file = tmp_path / "scope.json"
    Scope(path=str(scope_file)).save()
    args = SimpleNamespace(report_cmd="build", operator="op", sanitize=False, out=None,
                           html=None, engagement="x", scope_file=str(scope_file),
                           actions=None, from_authlog=False, authlog=None, key=None)
    assert cmd_report(args) == 2
    assert "--actions" in capsys.readouterr().out
