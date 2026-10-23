"""Per-radio engine assignment: every capture radio gets an engine, not just one."""
from redux.radio import Radio, Intent, decide
from redux.crack.capture import plan_capture_bindings, CaptureBinding

ONBOARD = Radio("wlan0", phy="phy0", bands=frozenset({"2.4"}), monitor=True,
                inject=False, driver="brcmfmac", onboard=True)
ALFA = Radio("wlan1", phy="phy1", bands=frozenset({"2.4", "5"}), monitor=True,
             inject=True, driver="mt76x2u", usb_gen=3)
SIXE = Radio("wlan2", phy="phy2", bands=frozenset({"2.4", "5", "6"}), monitor=True,
             inject=True, driver="mt7921u", usb_gen=3)


def _by_iface(bindings):
    return {b.iface: b for b in bindings}


def test_onboard_only_binds_the_scalpel_passively():
    a = decide([ONBOARD], Intent.HUNT)
    b = plan_capture_bindings(a, angryoxide_present=True, bettercap_present=True)
    assert len(b) == 1
    assert b[0].iface == "wlan0" and b[0].role == "capture"
    assert b[0].engine == "angryoxide"      # scalpel preferred
    assert b[0].mode == "passive"           # honest: brcmfmac can't inject


def test_two_radios_run_both_engines_side_by_side():
    a = decide([ONBOARD, ALFA], Intent.HUNT)
    b = plan_capture_bindings(a, angryoxide_present=True, bettercap_present=True)
    m = _by_iface(b)
    assert m["wlan1"].engine == "angryoxide" and m["wlan1"].mode == "active"   # Alfa = scalpel
    assert m["wlan0"].engine == "bettercap"                                    # onboard = nervous system
    # the whole point: both engines are actually running at once
    assert {x.engine for x in b} == {"angryoxide", "bettercap"}


def test_three_radios_every_radio_captures_on_its_own_band():
    a = decide([ONBOARD, ALFA, SIXE], Intent.HUNT)
    b = plan_capture_bindings(a, angryoxide_present=True, bettercap_present=True)
    m = _by_iface(b)
    assert len(b) == 3                                   # nothing idles
    assert m["wlan2"].role == "capture" and m["wlan2"].engine == "angryoxide"  # 6E leads active
    assert m["wlan2"].mode == "active"
    # bettercap placed on exactly one tandem radio; the rest are passive AngryOxide
    assert sum(x.engine == "bettercap" for x in b) == 1
    assert sum(x.engine == "angryoxide" for x in b) == 2
    # three radios, three distinct bands, all captured concurrently
    assert len({x.band for x in b}) == 3


def test_only_angryoxide_drives_every_radio():
    a = decide([ONBOARD, ALFA], Intent.HUNT)
    b = plan_capture_bindings(a, angryoxide_present=True, bettercap_present=False)
    assert all(x.engine == "angryoxide" for x in b)
    m = _by_iface(b)
    assert m["wlan1"].mode == "active" and m["wlan0"].mode == "passive"


def test_only_bettercap_is_the_honest_fallback():
    a = decide([ONBOARD, ALFA], Intent.HUNT)
    b = plan_capture_bindings(a, angryoxide_present=False, bettercap_present=True)
    assert all(x.engine == "bettercap" for x in b)


def test_no_engine_is_glass_box_not_a_crash():
    a = decide([ONBOARD, ALFA], Intent.HUNT)
    b = plan_capture_bindings(a, angryoxide_present=False, bettercap_present=False)
    assert b and all(x.engine is None for x in b)
    assert all("no capture engine" in x.reason for x in b)


def test_no_capture_radio_yields_no_bindings():
    a = decide([ONBOARD], Intent.ONLINE)       # online: onboard is uplink, not capture
    assert plan_capture_bindings(a, angryoxide_present=True, bettercap_present=True) == []


def test_binding_is_serializable_for_the_status_page():
    a = decide([ONBOARD, ALFA], Intent.HUNT)
    b = plan_capture_bindings(a, angryoxide_present=True, bettercap_present=True)
    d = b[0].to_dict()
    assert set(d) == {"iface", "role", "engine", "mode", "band", "reason"}
    assert isinstance(b[0], CaptureBinding)


def test_augur_capture_bindings_wires_the_full_assignment():
    from redux.core.augur import Augur
    a = Augur(radios=[ONBOARD, ALFA], intent=Intent.HUNT)
    binds = a.capture_bindings()
    m = {b["iface"]: b for b in binds}
    assert set(m) == {"wlan0", "wlan1"}
    assert m["wlan1"]["role"] == "capture" and m["wlan0"]["role"] == "scan"
    # bands carried through from the orchestrator (tandem spread across spectrum)
    assert m["wlan1"]["band"] != m["wlan0"]["band"]
    assert all(set(b) == {"iface", "role", "engine", "mode", "band", "reason"}
               for b in binds)
