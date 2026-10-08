from redux.geo.oui import label_mac, lookup_oui, oui_prefix


def test_prefix():
    assert oui_prefix("AA:BB:CC:DD:EE:FF") == "aabbcc"


def test_lookup():
    assert lookup_oui("aa:bb:cc:11:22:33") == "DemoVendor"
    assert "DemoVendor" in label_mac("aa:bb:cc:11:22:33")
    assert lookup_oui("ff:ff:ff:ff:ff:ff") is None
