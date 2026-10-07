"""Autonomous kill-chain operator — gating, planning, execution.

Pins the three gates (posture → scope → capability), the glass-box plan, and the
run path that advances only while steps succeed and feeds a CLEAN report when all
executed actions were in scope.
"""
from redux.operator import Operator, Phase
from redux.core import Scope, Beastcore
from redux.radio import Radio, Intent
from redux.report import build_report


ARMED = "00:11:22:33:44:55"
UNARMED = "aa:bb:cc:dd:ee:ff"


def _scope():
    s = Scope()
    s.add(ARMED, "bssid", job="op")
    return s


def _op(offense=True, engine=True):
    return Operator(_scope(), offense_enabled=offense, caps={"capture.handshake": engine})


# --- plan gating ------------------------------------------------------------- #

def test_plan_allows_armed_target_through_the_chain():
    steps = _op().plan([ARMED])
    chain = [s for s in steps if s.target == ARMED]
    assert chain and all(s.allowed for s in chain)
    assert {s.phase for s in chain} == set(Phase) - {Phase.RECON}


def test_plan_blocks_unarmed_target_at_capture_then_contingent():
    steps = _op().plan([UNARMED])
    cap = next(s for s in steps if s.target == UNARMED and s.phase is Phase.CAPTURE)
    assert cap.allowed is False and "not authorized" in cap.reason
    downstream = [s for s in steps if s.target == UNARMED and s.phase is not Phase.CAPTURE]
    assert all((not s.allowed) and "contingent" in s.reason for s in downstream)


def test_recon_is_always_allowed():
    recon = next(s for s in _op().plan([ARMED]) if s.phase is Phase.RECON)
    assert recon.allowed is True


# --- gate precedence --------------------------------------------------------- #

def test_posture_gate_precedes_scope():
    # offense off → even the armed target is blocked for posture, not scope
    cap = next(s for s in _op(offense=False).plan([ARMED]) if s.phase is Phase.CAPTURE)
    assert cap.allowed is False and "detection-only" in cap.reason


def test_capability_gate_blocks_capture_when_no_engine():
    cap = next(s for s in _op(engine=False).plan([ARMED]) if s.phase is Phase.CAPTURE)
    assert cap.allowed is False and "capability" in cap.reason


# --- run --------------------------------------------------------------------- #

def _execs(fail_on=None):
    def mk(name):
        def run(t):
            if fail_on == name:
                return False, f"{name} FAILED on {t}"
            return True, f"{name} ok on {t}"
        return run
    return {Phase.CAPTURE: mk("capture"), Phase.CRACK: mk("crack"),
            Phase.PIVOT_SCAN: mk("scan"), Phase.PIVOT_CRED: mk("cred"), Phase.LOOT: mk("loot")}


def test_run_executes_full_chain_on_armed_target():
    out = _op().run([ARMED], _execs(), recon=lambda: (True, "seen"))
    executed = [s for s in out["steps"] if s["executed"]]
    # recon + 5 chain phases
    assert len(executed) == 6
    assert out["summary"]["executed_steps"] == 6


def test_run_stops_chain_on_a_failed_step():
    out = _op().run([ARMED], _execs(fail_on="crack"), recon=lambda: (True, "seen"))
    phases = [s["phase"] for s in out["steps"] if s["executed"]]
    assert "capture" in phases and "crack" in phases
    assert "pivot_scan" not in phases      # crack failed → no pivot


def test_run_stops_when_executor_missing():
    execs = {Phase.CAPTURE: lambda t: (True, "cap ok")}   # no crack executor
    out = _op().run([ARMED], execs, recon=lambda: (True, "seen"))
    steps = out["steps"]
    crack = next(s for s in steps if s["phase"] == "crack")
    assert crack["executed"] is False and "no executor" in crack["reason"]


def test_run_log_feeds_a_clean_report_when_all_in_scope():
    out = _op().run([ARMED, UNARMED], _execs(), recon=lambda: (True, "seen"))
    rep = build_report("camp", "op", _scope(), out["log"])
    # the unarmed target was blocked (never executed) → log has only armed actions
    assert rep["integrity"] == "CLEAN" and rep["unauthorized_count"] == 0
    assert all(a.target == ARMED for a in out["log"])


def test_run_log_excludes_passive_recon():
    out = _op().run([ARMED], _execs(), recon=lambda: (True, "seen"))
    assert all(a.action != "wifi_recon" for a in out["log"])


# --- Beastcore integration --------------------------------------------------- #

def test_beastcore_campaign_plan_reflects_posture_and_engine():
    r = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=True, driver="mt76")
    bc = Beastcore(radios=[r], intent=Intent.RECON)       # no driver → no capture engine
    bc.scope = _scope()
    bc.apply_persona("red")
    plan = bc.campaign_plan([ARMED])
    cap = next(s for s in plan if s["phase"] == "capture")
    # armed + offense on, but no capture engine present → blocked on capability
    assert cap["allowed"] is False and "capability" in cap["reason"]