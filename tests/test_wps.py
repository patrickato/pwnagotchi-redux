"""WPS / Pixie-Dust: passive wash discovery + scope-gated, glass-box attack plan."""
from redux.wps import (
    WpsTarget, parse_wash, plan, authorize_wps, run_plan, _extract_psk,
)
from redux.core.scope import Scope


# ---- wash discovery (passive) ------------------------------------------- #

WASH = """\
BSSID               Ch  dBm  WPS  Lck  Vendor    ESSID
--------------------------------------------------------
AA:BB:CC:DD:EE:01   1   -40  2.0  No   AaBbCc    HomeNet
AA:BB:CC:DD:EE:02   6   -70  2.0  Yes  RalinkTe  Locked AP
not a wash row at all
"""


def test_parse_wash_lists_targets_and_lock_state():
    ts = parse_wash(WASH)
    assert len(ts) == 2
    by = {t.bssid: t for t in ts}
    assert by["aa:bb:cc:dd:ee:01"].channel == 1 and not by["aa:bb:cc:dd:ee:01"].locked
    assert by["aa:bb:cc:dd:ee:01"].ssid == "HomeNet"
    assert by["aa:bb:cc:dd:ee:02"].locked is True


# ---- authorize gate ------------------------------------------------------ #

def test_authorize_field_needs_scope_and_flag():
    ok, why = authorize_wps(Scope(), "aa:bb:cc:dd:ee:01", authorized=False, lab_mode=False)
    assert not ok and "scope" in why                      # empty scope, field -> refused


def test_authorize_field_in_scope_needs_authorized():
    s = Scope(); s.add("aa:bb:cc:dd:ee:01", job="lab")
    ok, why = authorize_wps(s, "aa:bb:cc:dd:ee:01", authorized=False, lab_mode=False)
    assert not ok and "authorized" in why
    ok2, _ = authorize_wps(s, "aa:bb:cc:dd:ee:01", authorized=True, lab_mode=False)
    assert ok2


def test_authorize_lab_approves_on_sight():
    ok, why = authorize_wps(Scope(lab_mode=True), "aa:bb:cc:dd:ee:09",
                            authorized=False, lab_mode=True)
    assert ok and "LAB" in why


# ---- plan ---------------------------------------------------------------- #

def test_plan_lab_builds_reaver_pixie():
    t = WpsTarget(bssid="aa:bb:cc:dd:ee:01", channel=6)
    p = plan(t, Scope(lab_mode=True), lab_mode=True)
    assert p.runnable and p.tool == "reaver"
    assert p.argv[:1] == ["reaver"] and "-b" in p.argv and "aa:bb:cc:dd:ee:01" in p.argv
    assert "-c" in p.argv and "6" in p.argv
    assert "-K" in p.argv                                 # pixie-dust


def test_plan_bully_variant():
    p = plan("aa:bb:cc:dd:ee:01", Scope(lab_mode=True), tool="bully", lab_mode=True)
    assert p.runnable and p.argv[0] == "bully" and "-d" in p.argv


def test_plan_field_refuses_without_scope():
    p = plan("aa:bb:cc:dd:ee:01", Scope(), lab_mode=False, authorized=True)
    assert not p.runnable and p.argv == [] and "scope" in p.reason


def test_plan_rejects_bad_bssid():
    p = plan("not-a-bssid", Scope(lab_mode=True), lab_mode=True)
    assert not p.runnable and "BSSID" in p.reason


def test_plan_warns_on_locked_ap():
    t = WpsTarget(bssid="aa:bb:cc:dd:ee:02", channel=6, locked=True)
    p = plan(t, Scope(lab_mode=True), lab_mode=True)
    assert p.runnable and "LOCKED" in p.reason


# ---- run (injected runner) ---------------------------------------------- #

class _Proc:
    def __init__(self, rc, out=b""): self.returncode = rc; self.stdout = out


def test_run_refuses_unrunnable_plan():
    p = plan("aa:bb:cc:dd:ee:01", Scope(), lab_mode=False)   # refused
    res = run_plan(p, runner=lambda *a, **k: _Proc(0))
    assert res["status"] == "refused"


def test_run_surfaces_recovered_key():
    p = plan("aa:bb:cc:dd:ee:01", Scope(lab_mode=True), lab_mode=True)
    out = b"[+] WPS PIN: '12345670'\n[+] WPA PSK: 'hunter2pass'\n"
    res = run_plan(p, runner=lambda *a, **k: _Proc(0, out))
    assert res["status"] == "recovered" and res["key"] == "hunter2pass"


def test_run_completed_without_key():
    p = plan("aa:bb:cc:dd:ee:01", Scope(lab_mode=True), lab_mode=True)
    res = run_plan(p, runner=lambda *a, **k: _Proc(0, b"nothing useful"))
    assert res["status"] == "completed"


def test_extract_psk():
    assert _extract_psk("[+] WPA PSK: 'secret'") == "secret"
    assert _extract_psk("nope") == ""
