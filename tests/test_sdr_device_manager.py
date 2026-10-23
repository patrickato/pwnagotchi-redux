"""SDR orchestration: two dongles run ISM and ADS-B at once; one picks a role."""
from redux.sdr.device_manager import (SdrDevice, SdrRole, plan_sdr_roles,
                                       parse_rtl_devices)

D0 = SdrDevice(0, serial="00000001")
D1 = SdrDevice(1, serial="00000002")


def test_two_dongles_run_both_sources_concurrently():
    a = plan_sdr_roles([D0, D1])
    assert a.roles[0] is SdrRole.ISM and a.roles[1] is SdrRole.ADSB
    assert a.device_for(SdrRole.ISM) == 0 and a.device_for(SdrRole.ADSB) == 1
    assert a.warnings == []


def test_one_dongle_takes_ism_and_asks_for_a_second_for_adsb():
    a = plan_sdr_roles([D0])
    assert a.roles[0] is SdrRole.ISM
    assert a.device_for(SdrRole.ADSB) is None
    assert any("needs its own dongle" in w for w in a.warnings)


def test_role_preference_is_honored():
    a = plan_sdr_roles([D0], want=(SdrRole.ADSB, SdrRole.ISM))
    assert a.roles[0] is SdrRole.ADSB


def test_third_dongle_is_spare():
    a = plan_sdr_roles([D0, D1, SdrDevice(2)])
    assert a.roles[2] is SdrRole.IDLE
    assert "spare" in a.reasons[2]


def test_no_dongle_warns_not_crashes():
    a = plan_sdr_roles([])
    assert a.roles == {} and any("no RTL-SDR" in w for w in a.warnings)


def test_parse_rtl_test_listing():
    text = ("Found 2 device(s):\n"
            "  0:  Realtek, RTL2838UHIDIR, SN: 00000001\n"
            "  1:  Realtek, RTL2832U, SN: 77771111\n"
            "\nUsing device 0: Generic RTL2832U OEM\n")
    devs = parse_rtl_devices(text)
    assert [d.index for d in devs] == [0, 1]
    assert devs[1].serial == "77771111" and "RTL2832U" in devs[1].name
    a = plan_sdr_roles(devs)
    assert a.roles[0] is SdrRole.ISM and a.roles[1] is SdrRole.ADSB
