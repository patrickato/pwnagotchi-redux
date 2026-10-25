"""Purple-team explain & defend knowledge base (engine + CLI)."""
from types import SimpleNamespace

from redux.intel.defend import (
    explain, list_techniques, remediation_for, TECHNIQUES, Technique,
)


def test_every_technique_is_well_formed():
    for key, t in TECHNIQUES.items():
        assert t.key == key
        assert t.what and t.detection and t.mitigation       # no empty sections
        assert all(isinstance(x, str) and x for x in t.detection + t.mitigation)


def test_explain_is_key_and_alias_insensitive():
    assert explain("deauth").key == "deauth"
    assert explain("Deauth").key == "deauth"
    assert explain("evil-twin").key == "evil_twin"           # hyphen normalised
    assert explain("wpa2-psk").key == "handshake"            # finding-kind alias
    assert explain("pmkid").key == "pmkid"


def test_unknown_technique_returns_none():
    assert explain("sqlinjection") is None
    assert explain("") is None


def test_deauth_defense_names_pmf():
    t = explain("deauth")
    assert any("PMF" in m or "802.11w" in m for m in t.mitigation)
    assert "802.11w (PMF)" in t.standards


def test_pmkid_and_handshake_point_at_wpa3():
    assert any("WPA3" in s for s in explain("pmkid").standards)
    assert any("WPA3" in s for s in explain("handshake").standards)


def test_forbidden_emitters_documented_but_flagged_not_emitted():
    for key in ("beacon_flood", "jamming"):
        t = explain(key)
        assert t is not None
        assert "NOT emitted" in t.name or "NOT emitted" in t.what


def test_remediation_for_finding_kinds():
    assert remediation_for("handshake")
    assert remediation_for("pmkid")
    assert remediation_for("nonsense") == ()                 # no fabricated fix


def test_list_techniques_covers_core_set():
    keys = {k for k, _ in list_techniques()}
    assert {"deauth", "pmkid", "handshake", "evil_twin", "karma", "wps",
            "netrecon", "cred_spray"} <= keys


# --- CLI -------------------------------------------------------------------- #

def test_cmd_explain_lists_when_no_arg(capsys):
    from redux.cli import cmd_explain
    assert cmd_explain(SimpleNamespace(technique="", json=False)) == 0
    out = capsys.readouterr().out
    assert "deauth" in out and "evil_twin" in out


def test_cmd_explain_one_technique_human_and_json(capsys):
    from redux.cli import cmd_explain
    import json as _json
    assert cmd_explain(SimpleNamespace(technique="deauth", json=False)) == 0
    out = capsys.readouterr().out
    assert "HOW IT'S DETECTED" in out and "HOW TO DEFEND" in out
    assert cmd_explain(SimpleNamespace(technique="deauth", json=True)) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["key"] == "deauth" and payload["mitigation"]


def test_cmd_explain_unknown_returns_nonzero(capsys):
    from redux.cli import cmd_explain
    assert cmd_explain(SimpleNamespace(technique="xyzzy", json=False)) == 2
    assert "unknown technique" in capsys.readouterr().out
