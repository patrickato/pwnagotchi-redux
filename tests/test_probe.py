from redux.radio.probe import parse_iw_dev, parse_iw_phy, build_radios

IW_PHY = """\
Wiphy phy0
\twiphy index: 0
\tSupported interface modes:
\t\t * managed
\t\t * AP
\t\t * monitor
\tBand 1:
\t\tFrequencies:
\t\t\t* 2412 MHz [1] (20.0 dBm)
\t\t\t* 2437 MHz [6] (20.0 dBm)
\t\t\t* 2462 MHz [11] (20.0 dBm)
Wiphy phy1
\twiphy index: 1
\tSupported interface modes:
\t\t * managed
\t\t * monitor
\tBand 1:
\t\tFrequencies:
\t\t\t* 2412 MHz [1] (20.0 dBm)
\tBand 2:
\t\tFrequencies:
\t\t\t* 5180 MHz [36] (23.0 dBm)
\t\t\t* 5745 MHz [149] (30.0 dBm)
"""

IW_DEV = """\
phy#1
\tInterface wlan1
\t\tifindex 5
\t\ttype managed
phy#0
\tInterface wlan0
\t\tifindex 3
\t\ttype managed
"""

META = {
    "phy0": {"driver": "brcmfmac", "usb_gen": None},
    "phy1": {"driver": "mt76x2u", "usb_gen": 3},
}


def test_parse_iw_dev_maps_phy_to_iface():
    d = parse_iw_dev(IW_DEV)
    assert d["phy0"] == "wlan0"
    assert d["phy1"] == "wlan1"


def test_parse_iw_phy_bands_and_monitor():
    p = parse_iw_phy(IW_PHY)
    assert p["phy0"]["monitor"] is True
    assert p["phy0"]["bands"] == {"2.4"}
    assert p["phy1"]["bands"] == {"2.4", "5"}


def test_build_radios_onboard_vs_injector():
    radios = {r.iface: r for r in build_radios(IW_PHY, IW_DEV, META)}

    onboard = radios["wlan0"]
    assert onboard.onboard is True
    assert onboard.inject is False          # brcmfmac: monitor yes, inject no
    assert onboard.bands == frozenset({"2.4"})

    alfa = radios["wlan1"]
    assert alfa.onboard is False
    assert alfa.monitor is True
    assert alfa.inject is True              # mt76x2u is injection-capable
    assert alfa.high_draw is True
    assert alfa.usb_gen == 3
    assert alfa.bands == frozenset({"2.4", "5"})

def test_multi_interface_phy_prefers_existing_monitor_vif():
    iw_dev = """phy#0
    Interface wlan0
        ifindex 3
        type managed
    Interface wlan0mon
        ifindex 7
        type monitor
    """
    selected = parse_iw_dev(iw_dev)
    assert selected == {"phy0": "wlan0mon"}
    radios = build_radios(IW_PHY, iw_dev, META)
    assert [r.iface for r in radios] == ["wlan0mon"]
    assert radios[0].phy == "phy0"


def test_multi_interface_phy_preserves_monitor_when_managed_follows():
    iw_dev = """phy#1
    Interface wlan1mon
        type monitor
    Interface wlan1
        type managed
    """
    assert parse_iw_dev(iw_dev) == {"phy1": "wlan1mon"}


def test_phy_without_netdev_is_not_reported_as_radio():
    assert build_radios(IW_PHY, "phy#0\nphy#1\n", META) == []
    partial = build_radios(IW_PHY, "phy#1\nInterface wlan1\n    type managed\n", META)
    assert len(partial) == 1 and partial[0].iface == "wlan1"
