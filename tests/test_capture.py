"""Capture providers — AngryOxide scalpel + bettercap, behind the capability graph.

Pins the two invariants that keep it safe: Scope decides WHERE (empty scope → no
broad sweep), and posture maps to transmit (no offense → --notransmit passive).
All injected (which/runner/driver), so no binary and no radio are needed.
"""
from redux.crack import (
    AngryOxideProvider, AngryOxideConfig, BettercapProvider,
    select_capture_provider, register_capture_providers,
)
from redux.core import Scope, CapabilityGraph, Cap, Beastcore
from redux.radio import Radio, Intent


def _armed_scope():
    s = Scope()
    s.add("00:11:22:33:44:55", "bssid", job="lab")
    s.add("CorpNet", "ssid", job="lab")
    s.add("192.168.1.0/24", "cidr", job="lab")   # not a Wi-Fi capture target → ignored
    return s


# --- honest tool-absence ----------------------------------------------------- #

def test_available_reflects_binary_presence():
    present = AngryOxideProvider(which=lambda b: "/usr/bin/angryoxide")
    absent = AngryOxideProvider(which=lambda b: None)
    assert present.available() is True and absent.available() is False


def test_run_without_binary_does_not_run():
    p = AngryOxideProvider(which=lambda b: None)
    out = p.run(_armed_scope(), active=True, runner=lambda argv: (_ for _ in ()).throw(AssertionError("ran!")))
    assert out["ran"] is False and out["available"] is False


# --- Scope decides WHERE ----------------------------------------------------- #

def test_empty_scope_refuses_broad_attack():
    p = AngryOxideProvider(which=lambda b: "/usr/bin/angryoxide")
    plan = p.plan(Scope(), active=True)
    assert plan.runnable is False
    assert "will not run a broad" in plan.reason.lower() or "will NOT run a broad" in plan.reason


def test_plan_passes_only_armed_wifi_targets():
    p = AngryOxideProvider(which=lambda b: "/usr/bin/angryoxide")
    plan = p.plan(_armed_scope(), active=True)
    assert plan.runnable is True
    assert set(plan.targets) == {"00:11:22:33:44:55", "corpnet"} or \
           set(plan.targets) == {"00:11:22:33:44:55", "CorpNet"}
    # every armed target appears as a -t, the CIDR does not
    assert plan.argv.count("-t") == 2
    assert "192.168.1.0/24" not in plan.argv


# --- posture maps to transmit ------------------------------------------------ #

def test_active_posture_attacks_passive_posture_does_not_transmit():
    p = AngryOxideProvider(which=lambda b: "/usr/bin/angryoxide")
    active = p.plan(_armed_scope(), active=True)
    passive = p.plan(_armed_scope(), active=False)
    assert "--notransmit" not in active.argv and active.passive is False
    assert "--notransmit" in passive.argv and passive.passive is True


def test_run_spawns_with_constructed_argv():
    p = AngryOxideProvider(which=lambda b: "/usr/bin/angryoxide",
                           config=AngryOxideConfig(iface="wlan1", band=5))
    seen = {}
    out = p.run(_armed_scope(), active=True, runner=lambda argv: seen.setdefault("argv", argv))
    assert out["ran"] is True and seen["argv"] == out["argv"]
    assert "--headless" in out["argv"] and "--autoexit" in out["argv"]


# --- bettercap fallback + selection ------------------------------------------ #

def test_bettercap_available_iff_driver_and_is_the_fallback():
    assert BettercapProvider(driver=None).available() is False
    bc = BettercapProvider(driver=object())
    assert bc.available() is True
    # bettercap can run passive recon even with an empty scope (honest fallback)
    assert bc.plan(Scope(), active=True).runnable is True


def test_selection_prefers_angryoxide_when_present_else_bettercap():
    ao = AngryOxideProvider(which=lambda b: "/usr/bin/angryoxide")
    bc = BettercapProvider(driver=object())
    chosen, _ = select_capture_provider([ao, bc])
    assert chosen.name == "angryoxide"
    chosen2, _ = select_capture_provider([AngryOxideProvider(which=lambda b: None), bc])
    assert chosen2.name == "bettercap"
    none, reason = select_capture_provider([AngryOxideProvider(which=lambda b: None),
                                            BettercapProvider(driver=None)])
    assert none is None and "no capture engine" in reason


# --- capability graph registration ------------------------------------------- #

def test_register_capture_providers_sets_active_provider():
    g = CapabilityGraph()
    register_capture_providers(g, angryoxide_present=True, bettercap_present=True)
    # AngryOxide registered first → preferred active provider
    assert g.active_provider(Cap.CAPTURE_HANDSHAKE).name == "angryoxide-capture"
    g2 = CapabilityGraph()
    register_capture_providers(g2, angryoxide_present=False, bettercap_present=True)
    assert g2.active_provider(Cap.CAPTURE_HANDSHAKE).name == "bettercap-capture"


# --- Beastcore integration: persona posture flows through -------------------- #

class _FakeDriver:
    """No-op bettercap driver so the capture path has an available fallback engine
    (AngryOxide is correctly absent in the sandbox)."""
    def __getattr__(self, name):
        return lambda *a, **k: {}


def _bc():
    r = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=True,
              driver="mt76", onboard=False)
    bc = Beastcore(radios=[r], intent=Intent.RECON, driver=_FakeDriver())
    bc.scope = _armed_scope()
    return bc


def test_beastcore_capture_plan_blue_persona_is_passive():
    bc = _bc()
    bc.apply_persona("blue")        # detection-only → offense off
    plan = bc.capture_plan()
    assert plan["offense_enabled"] is False and plan["passive"] is True


def test_beastcore_capture_plan_active_with_armed_scope():
    bc = _bc()
    bc.apply_persona("red")         # active posture
    plan = bc.capture_plan()
    assert plan["offense_enabled"] is True
    assert plan["selected_engine"] in ("angryoxide", "bettercap")


def test_beastcore_doctor_reports_capture_engine_health():
    r = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=True, driver="mt76")
    # no driver / no AngryOxide binary → capture engine is ACTION (honest, not clean)
    bare = Beastcore(radios=[r], intent=Intent.RECON)
    areas = {f["area"]: f["status"] for f in bare.doctor_report()["findings"]}
    assert areas.get("capture engine") == "action"
    # with the bettercap driver up → capture engine OK
    up = Beastcore(radios=[r], intent=Intent.RECON, driver=_FakeDriver())
    areas2 = {f["area"]: f["status"] for f in up.doctor_report()["findings"]}
    assert areas2.get("capture engine") == "ok"
