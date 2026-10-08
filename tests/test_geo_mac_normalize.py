from redux.geo.mac_normalize import is_locally_administered, is_valid_mac, normalize_mac


def test_normalize():
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert is_valid_mac("aa:bb:cc:dd:ee:ff")
    assert not is_valid_mac("zz:zz")
    assert is_locally_administered("02:00:00:00:00:00")
