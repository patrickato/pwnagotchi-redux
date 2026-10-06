from redux.radio import Radio, Role, Intent, decide, on_hotplug, on_unplug

ONBOARD = Radio("wlan0", phy="phy0", bands=frozenset({"2.4"}), monitor=True,
                inject=False, driver="brcmfmac", onboard=True)
ALFA = Radio("wlan1", phy="phy1", bands=frozenset({"2.4", "5"}), monitor=True,
             inject=True, driver="mt76x2u", usb_gen=3, high_draw=True)
ALFA_USB2 = Radio("wlan1", phy="phy1", bands=frozenset({"2.4", "5"}), monitor=True,
                  inject=True, driver="mt76x2u", usb_gen=2, high_draw=True)
SIXE = Radio("wlan2", phy="phy2", bands=frozenset({"2.4", "5", "6"}), monitor=True,
             inject=True, driver="mt7921u", usb_gen=3)


def test_online_prefers_onboard_uplink():
    a = decide([ONBOARD, ALFA], Intent.ONLINE)
    assert a.roles["wlan0"] is Role.UPLINK
    assert a.roles["wlan1"] is Role.IDLE


def test_hunt_promotes_injector_and_keeps_onboard_online():
    a = decide([ONBOARD, ALFA], Intent.HUNT)
    assert a.roles["wlan1"] is Role.CAPTURE      # Alfa injects -> capture
    assert a.roles["wlan0"] is Role.UPLINK       # onboard kept online
    assert "inject" in a.reasons["wlan1"]


def test_hunt_with_only_onboard_degrades_passive_and_warns():
    a = decide([ONBOARD], Intent.HUNT)
    assert a.roles["wlan0"] is Role.CAPTURE
    assert any("degraded to passive" in w for w in a.warnings)


def test_brownout_warning_on_usb2_highdraw():
    a = decide([ONBOARD, ALFA_USB2], Intent.HUNT)
    assert a.roles["wlan1"] is Role.CAPTURE
    assert any("USB 2.0" in w for w in a.warnings)


def test_sixe_beats_alfa_for_capture():
    a = decide([ONBOARD, ALFA, SIXE], Intent.HUNT)
    assert a.roles["wlan2"] is Role.CAPTURE      # 6 GHz-capable wins


def test_recon_is_passive_capture():
    a = decide([ONBOARD, ALFA], Intent.RECON)
    cap = [i for i, r in a.roles.items() if r is Role.CAPTURE][0]
    assert "passive" in a.reasons[cap]


def test_hotplug_promotes_then_unplug_falls_back():
    promoted = on_hotplug([ONBOARD], ALFA, Intent.HUNT)
    assert promoted.roles["wlan1"] is Role.CAPTURE
    assert promoted.roles["wlan0"] is Role.UPLINK
    fallback = on_unplug([ONBOARD], Intent.HUNT)
    assert fallback.roles["wlan0"] is Role.CAPTURE
