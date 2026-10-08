from redux.geo.mac_normalize import is_valid_mac, normalize_mac


def test_normalize():
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aabbccddeeff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("bad") == ""
    assert is_valid_mac("aa:bb:cc:dd:ee:ff")
    assert not is_valid_mac("zz:zz:zz:zz:zz:zz")
