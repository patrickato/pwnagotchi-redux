"""Engagement report — the chain-of-authorization deliverable.

Pins the integrity behaviour: in-scope actions are authorized, out-of-scope ones
are surfaced as UNAUTHORIZED (never hidden), and the header verdict reflects it.
"""
from redux.report import EngagementAction, build_report, render_markdown
from redux.core import Scope, Augur
from redux.radio import Intent


def _scope():
    s = Scope()
    s.add("00:11:22:33:44:55", "bssid", job="job1")
    s.add("10.0.0.0/24", "cidr", job="job1")
    s.add("CorpNet", "ssid", job="job1")
    return s


def _acts():
    return [
        EngagementAction(100.0, "handshake_capture", "00:11:22:33:44:55", "cap", "got it"),
        EngagementAction(200.0, "net_scan", "10.0.0.50", "enum", "5 hosts"),
        EngagementAction(300.0, "deauth", "aa:bb:cc:dd:ee:ff", "stray", ""),   # NOT armed
    ]


def test_authorized_actions_pass_and_out_of_scope_is_flagged():
    rep = build_report("eng", "op", _scope(), _acts(), now=1000.0)
    acts = {l["action"]: l for l in rep["activity"]}
    assert acts["handshake_capture"]["authorized"] is True
    assert acts["net_scan"]["authorized"] is True            # ip inside an armed CIDR
    assert acts["deauth"]["authorized"] is False             # unarmed AP
    assert rep["integrity"] == "FLAGGED" and rep["unauthorized_count"] == 1


def test_clean_integrity_when_all_authorized():
    acts = [EngagementAction(100.0, "handshake_capture", "00:11:22:33:44:55", "cap")]
    rep = build_report("eng", "op", _scope(), acts, now=1000.0)
    assert rep["integrity"] == "CLEAN" and rep["unauthorized_count"] == 0


def test_attack_tags_attached_and_unmapped_is_honest():
    acts = [EngagementAction(1.0, "deauth", "00:11:22:33:44:55"),
            EngagementAction(2.0, "something_custom", "00:11:22:33:44:55")]
    rep = build_report("eng", "op", _scope(), acts, now=10.0)
    by = {l["action"]: l for l in rep["activity"]}
    assert by["deauth"]["attack_ids"] == ["T1498"]
    assert by["something_custom"]["attack_ids"] == [] and by["something_custom"]["phase"] == "(unmapped)"


def test_no_scope_means_unauthorized():
    rep = build_report("eng", "op", None, [EngagementAction(1.0, "deauth", "00:11:22:33:44:55")])
    assert rep["activity"][0]["authorized"] is False
    assert "no scope" in rep["activity"][0]["auth_reason"]


def test_activity_is_time_ordered():
    acts = [EngagementAction(300.0, "deauth", "00:11:22:33:44:55"),
            EngagementAction(100.0, "wifi_recon", "00:11:22:33:44:55")]
    rep = build_report("eng", "op", _scope(), acts, now=1000.0)
    assert [l["ts"] for l in rep["activity"]] == [100.0, 300.0]


# --- sanitization ------------------------------------------------------------ #

def test_sanitize_pseudonymizes_identifiers():
    rep = build_report("eng", "op", _scope(), _acts(), sanitize=True, now=1000.0)
    md = render_markdown(rep)
    assert rep["sanitized"] is True
    # the real armed BSSID and the real attacked BSSID must not appear verbatim
    assert "00:11:22:33:44:55" not in md
    assert "aa:bb:cc:dd:ee:ff" not in md
    # CIDRs are not personally identifying → kept
    assert "10.0.0.0/24" in md


# --- markdown rendering ------------------------------------------------------ #

def test_markdown_has_sections_and_flag_marker():
    md = render_markdown(build_report("ACME test", "patrick", _scope(), _acts(), now=1000.0))
    assert "# Engagement report — ACME test" in md
    assert "## Authorization (central Scope)" in md
    assert "## Activity (chain of authorization)" in md
    assert "⚠ UNAUTHORIZED" in md               # the stray deauth is visibly flagged
    assert "Integrity:** FLAGGED" in md


# --- Augur integration --------------------------------------------------- #

def test_augur_engagement_report_uses_live_scope():
    bc = Augur(radios=None, intent=Intent.RECON)
    bc.scope = _scope()
    rep = bc.engagement_report("eng", "patrick", _acts())
    assert rep["integrity"] == "FLAGGED"
    assert rep["authorization"]["summary"]["active"] == 3
    assert "findings" in rep
