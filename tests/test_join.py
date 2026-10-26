"""Wi-Fi JOIN — associate with a recovered key, take a lease, derive the subnet."""
import subprocess

from redux.netrecon.join import WifiJoiner, JoinResult, _cidr_from, _parse_lease


def _cp(argv, rc=0, out="", err=""):
    return subprocess.CompletedProcess(argv, rc, out, err)


def _good_runner(argv, **kw):
    cmd = argv[0]
    if cmd == "wpa_passphrase":
        return _cp(argv, 0, 'network={\n\tssid="dad24"\n\tpsk=deadbeefcafe\n}\n')
    if cmd == "wpa_supplicant":
        return _cp(argv, 0)
    if cmd == "dhclient":
        return _cp(argv, 0)
    if cmd == "ip" and "addr" in argv:
        return _cp(argv, 0, "    inet 192.168.1.37/24 brd 192.168.1.255 scope global wlan0")
    if cmd == "ip" and "route" in argv:
        return _cp(argv, 0, "default via 192.168.1.1 dev wlan0")
    return _cp(argv, 0)


def test_cidr_and_lease_helpers():
    assert _cidr_from("192.168.1.37", "255.255.255.0") == "192.168.1.0/24"
    assert _cidr_from("10.0.5.9", "255.255.0.0") == "10.0.0.0/16"
    assert _cidr_from("nonsense", "255.255.255.0") == ""     # never guesses
    ip, mask, gw = _parse_lease("inet 192.168.1.37/24\ndefault via 192.168.1.1 dev wlan0")
    assert ip == "192.168.1.37" and mask == "255.255.255.0" and gw == "192.168.1.1"


def test_join_associates_and_derives_subnet():
    j = WifiJoiner(iface="wlan0", runner=_good_runner)
    r = j.join("dad24", "supersecretpsk")
    assert isinstance(r, JoinResult) and r.joined is True
    assert r.ip == "192.168.1.37" and r.subnet == "192.168.1.0/24" and r.gateway == "192.168.1.1"
    assert "dad24" in r.reason


def test_join_rejects_bad_psk_without_touching_the_radio():
    calls = []
    def spy(argv, **kw):
        calls.append(argv)
        return _cp(argv, 0)
    r = WifiJoiner(iface="wlan0", runner=spy).join("dad24", "short")   # <8 chars
    assert r.joined is False and "PSK must be 8-63" in r.reason
    assert calls == []                                    # refused before any subprocess


def test_join_reports_associated_but_no_lease():
    def no_lease(argv, **kw):
        if argv[0] == "wpa_passphrase":
            return _cp(argv, 0, 'network={\n\tpsk=abc\n}\n')
        return _cp(argv, 0)        # association ok, but ip addr yields no inet
    r = WifiJoiner(iface="wlan0", runner=no_lease).join("dad24", "password123")
    assert r.joined is False and "no DHCP lease" in r.reason


def test_join_rejects_invalid_iface():
    r = WifiJoiner(iface="bad iface!", runner=_good_runner).join("dad24", "password123")
    assert r.joined is False and "invalid uplink interface" in r.reason
