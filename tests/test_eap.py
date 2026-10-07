"""WPA-Enterprise EAP capture — gating + crackable-line conversion.

Pins the aiming/posture gate (SSID must be in Scope, offense active, authorized)
and the MSCHAPv2 → hashcat/John conversion.
"""
from redux.eap import (
    MschapV2Credential, GtcCredential, parse_hostapd_wpe,
    EapHarvester, EapConfig, authorize_eap,
)
from redux.core import Scope, Beastcore
from redux.radio import Intent


# --- crackable artifacts ----------------------------------------------------- #

def test_mschapv2_to_hashcat_and_john():
    c = MschapV2Credential("CORP\\alice", "1122334455667788",
                           "AABBCCDDEEFF00112233445566778899AABBCCDDEEFF0011")
    assert c.hashcat_5500() == ("CORP\\alice::::aabbccddeeff00112233445566778899aabbccddeeff0011:"
                                "1122334455667788")
    assert c.john_netntlm().startswith("CORP\\alice:$NETNTLM$1122334455667788$")


def test_gtc_credential_is_cleartext():
    assert GtcCredential("bob", "Hunter2").as_line() == "bob:Hunter2"


def test_parse_hostapd_wpe_blocks():
    log = ("username: alice\nchallenge: 1122334455667788\n"
           "response: aabbccddeeff00112233445566778899aabbccddeeff0011\n"
           "username: bob\nchallenge: 99aabbccddeeff00\n"
           "response: 0011223344556677889900112233445566778899aabbccdd\n")
    creds = parse_hostapd_wpe(log)
    assert len(creds) == 2 and creds[0].username == "alice" and creds[1].username == "bob"
    assert creds[0].hashcat_5500().startswith("alice::::")


# --- the gate ---------------------------------------------------------------- #

def _scope():
    s = Scope()
    s.add("CorpWiFi", "ssid", job="eng")
    return s


def test_gate_requires_authorized_active_and_scope():
    sc = _scope()
    assert authorize_eap(sc, "CorpWiFi", authorized=False, active=True)[0] is False
    assert authorize_eap(sc, "CorpWiFi", authorized=True, active=False)[0] is False
    assert authorize_eap(sc, "RandomCafe", authorized=True, active=True)[0] is False
    ok, reason = authorize_eap(sc, "CorpWiFi", authorized=True, active=True)
    assert ok is True and "authorized" in reason


def test_gate_without_scope_refuses():
    assert authorize_eap(None, "X", authorized=True, active=True)[0] is False


# --- harvester plan ---------------------------------------------------------- #

def test_plan_runnable_only_when_gate_passes():
    h = EapHarvester(which=lambda b: "/usr/bin/hostapd-mana")
    good = h.plan(_scope(), "CorpWiFi", authorized=True, active=True)
    assert good.runnable is True
    assert "--ssid" in good.argv and "CorpWiFi" in good.argv
    assert "PEAP" in " ".join(good.argv)
    bad = h.plan(_scope(), "CorpWiFi", authorized=False, active=True)
    assert bad.runnable is False and bad.argv == []


def test_available_is_honest():
    assert EapHarvester(which=lambda b: None).available() is False
    assert EapHarvester(which=lambda b: "/usr/bin/hostapd-mana").available() is True


# --- Beastcore posture flow -------------------------------------------------- #

def _bc():
    bc = Beastcore(radios=None, intent=Intent.RECON)
    bc.scope = _scope()
    return bc


def test_beastcore_eap_plan_blue_persona_refuses():
    bc = _bc(); bc.apply_persona("blue")
    plan = bc.eap_plan("CorpWiFi", authorized=True)
    assert plan["runnable"] is False and "passive" in plan["reason"]


def test_beastcore_eap_plan_red_persona_runs_when_armed_and_authorized():
    bc = _bc(); bc.apply_persona("red")
    plan = bc.eap_plan("CorpWiFi", authorized=True)
    assert plan["runnable"] is True and "hashcat -m 5500" in plan["reason"]
