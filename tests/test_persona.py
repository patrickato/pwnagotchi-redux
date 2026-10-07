"""Personas — one box, pick your hat.

Pins the spine behaviour and the one safety invariant that matters: a persona's
posture is an EXTRA gate, never a looser one. Switching personas can only tighten
what can fire; Scope always decides WHERE.
"""
import pytest

from redux.core import persona as P
from redux.core import Augur, Posture, Scope
from redux.radio import Radio, Intent


def test_builtins_present_and_summarizable():
    for name in ("recon", "red", "blue", "purple", "mesh", "sigint"):
        assert name in P.names()
    rows = P.summarize()
    assert {r["name"] for r in rows} == set(P.names())
    assert all(r["reason"] for r in rows)   # every persona explains itself


def test_get_is_case_insensitive_and_validates():
    assert P.get("BLUE").name == "blue"
    with pytest.raises(KeyError):
        P.get("mauve")


def test_posture_drives_offense_availability():
    assert P.get("red").offense_available is True
    assert P.get("purple").offense_available is True
    assert P.get("blue").offense_available is False      # detection-only
    assert P.get("recon").offense_available is False     # passive
    assert P.get("blue").posture is Posture.DETECTION_ONLY


def _bc():
    r = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False,
              driver="brcmfmac", onboard=True)
    return Augur(radios=[r], intent=Intent.RECON)


def test_no_persona_means_scope_only_governance_offense_available():
    bc = _bc()
    assert bc.persona() is None
    assert bc.offense_enabled() is True       # no extra gate until a persona is applied


def test_apply_persona_sets_intent_and_reports_changes():
    bc = _bc()
    rec = bc.apply_persona("red")
    assert bc.persona().name == "red"
    assert bc.supervisor.intent is Intent.HUNT
    assert rec["changed"]["intent"]["to"] == "hunt"
    assert rec["reason"]


def test_blue_persona_hard_disables_offense_regardless_of_scope():
    bc = _bc()
    # arm a target in Scope — under red that target would be fireable
    bc.scope.add("192.168.1.0/24", "cidr", job="lab")
    bc.apply_persona("red")
    assert bc.offense_enabled() is True
    # flip to blue: offense is hard-off even though Scope still has an armed target
    bc.apply_persona("blue")
    assert bc.offense_enabled() is False
    assert bc.scope.permits(ip="192.168.1.5") is True     # Scope is unchanged...
    # ...the gate that closed is posture, not Scope — posture only tightens


def test_status_surfaces_persona_and_posture():
    bc = _bc()
    bc.apply_persona("purple")
    st = bc.status()
    assert st["persona"] == "purple" and st["posture"] == "active"
    assert st["offense_enabled"] is True


def test_switching_personas_can_only_tighten_then_restore():
    bc = _bc()
    bc.apply_persona("blue")
    assert bc.offense_enabled() is False
    bc.apply_persona("purple")
    assert bc.offense_enabled() is True       # explicit re-widen is a deliberate apply, never implicit
