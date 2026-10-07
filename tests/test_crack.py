"""capture→crack pipeline — scope-gated, injected cracker, honest tool-absence."""
import pytest

from redux.crack import crack, CrackResult, ReplayCracker, AircrackCracker, CrackRefused
from redux.core import Scope


def _files(tmp_path):
    cap = tmp_path / "handshake.pcap"; cap.write_bytes(b"\x00capture\x00")
    wl = tmp_path / "words.txt"; wl.write_text("hunter2\ncorrecthorse\n")
    return str(cap), str(wl)


def test_replay_cracker_recovers_a_key(tmp_path):
    cap, wl = _files(tmp_path)
    cracker = ReplayCracker(found={"aa:bb:cc:dd:ee:ff": "correcthorse"})
    r = crack(cap, wl, bssid="AA:BB:CC:DD:EE:FF", cracker=cracker)
    assert r.cracked is True and r.psk == "correcthorse" and r.tool == "replay"


def test_replay_cracker_key_not_in_wordlist(tmp_path):
    cap, wl = _files(tmp_path)
    r = crack(cap, wl, bssid="aa:bb:cc:dd:ee:ff", cracker=ReplayCracker())
    assert r.cracked is False and "not in wordlist" in r.reason


def test_scope_gate_refuses_unauthorized_target(tmp_path):
    cap, wl = _files(tmp_path)
    scope = Scope()                      # empty
    with pytest.raises(CrackRefused):
        crack(cap, wl, bssid="aa:bb:cc:dd:ee:ff", scope=scope, cracker=ReplayCracker())


def test_scope_gate_allows_authorized_target(tmp_path):
    cap, wl = _files(tmp_path)
    scope = Scope(); scope.add("aa:bb:cc:dd:ee:ff", job="acme")
    cracker = ReplayCracker(found={"aa:bb:cc:dd:ee:ff": "sae-pw"})
    r = crack(cap, wl, bssid="aa:bb:cc:dd:ee:ff", scope=scope, cracker=cracker)
    assert r.cracked is True and r.psk == "sae-pw"


def test_missing_capture_is_honest_not_a_false_no(tmp_path):
    _, wl = _files(tmp_path)
    r = crack(str(tmp_path / "nope.pcap"), wl, bssid="aa:bb:cc:dd:ee:ff", cracker=ReplayCracker())
    assert r.cracked is False and r.available is False and "not found" in r.reason


def test_missing_wordlist_is_honest(tmp_path):
    cap, _ = _files(tmp_path)
    r = crack(cap, str(tmp_path / "nope.txt"), bssid="aa:bb:cc:dd:ee:ff", cracker=ReplayCracker())
    assert r.cracked is False and r.available is False and "wordlist not found" in r.reason


def test_aircrack_absent_reports_unavailable_not_fake_failure(tmp_path, monkeypatch):
    cap, wl = _files(tmp_path)
    monkeypatch.setattr("redux.crack.pipeline.shutil.which", lambda _b: None)
    r = AircrackCracker().run(cap, wl, bssid="aa:bb:cc:dd:ee:ff")
    assert r.cracked is False and r.available is False
    assert "not installed" in r.reason and "Kali pack" in r.reason
