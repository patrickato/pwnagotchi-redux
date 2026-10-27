"""MAC randomization for Augur's own radios — generate + apply, no fingerprint."""
import random
import subprocess

from redux.radio.macrand import (
    random_mac, set_mac_commands, MacRandomizer, VENDOR_OUIS, _MAC,
)


def test_vendor_mode_uses_a_plausible_oui():
    rng = random.Random(1)
    mac = random_mac(mode="vendor", rng=rng)
    assert _MAC.fullmatch(mac)
    assert mac[:8] in VENDOR_OUIS                 # real-vendor OUI prefix


def test_laa_mode_sets_the_locally_administered_bit():
    mac = random_mac(mode="laa", rng=random.Random(2))
    assert _MAC.fullmatch(mac)
    first = int(mac[:2], 16)
    assert first & 0x02                           # locally-administered bit set
    assert not (first & 0x01)                     # unicast (multicast bit clear)


def test_randomization_actually_varies():
    rng = random.Random(3)
    macs = {random_mac(rng=rng) for _ in range(20)}
    assert len(macs) > 1                          # not a constant fingerprint


def test_set_mac_commands_down_set_up():
    cmds = set_mac_commands("wlan0", "a4:83:e7:11:22:33")
    assert cmds[0][-1] == "down" and cmds[-1][-1] == "up"
    assert ["ip", "link", "set", "dev", "wlan0", "address", "a4:83:e7:11:22:33"] in cmds


def test_randomizer_applies_via_injected_runner():
    calls = []
    def runner(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    r = MacRandomizer(runner=runner, rng=random.Random(5))
    res = r.randomize("wlan1")
    assert res.ok and _MAC.fullmatch(res.mac)
    # it brought the link down, set the address, and brought it back up
    assert calls[0][-1] == "down" and calls[1][-2] == "address" and calls[-1][-1] == "up"


def test_randomizer_reports_failure_honestly():
    def runner(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, "", "Operation not permitted")
    res = MacRandomizer(runner=runner).randomize("wlan0")
    assert res.ok is False and "failed" in res.reason


def test_randomizer_rejects_bad_iface():
    res = MacRandomizer(runner=lambda *a, **k: None).randomize("bad iface!")
    assert res.ok is False and "invalid interface" in res.reason
