"""Distributed Scope sync — convergence AND forgery rejection.

The headline safety property: a delta not signed with the swarm key is rejected
and never touches the Scope. Plus last-writer-wins convergence under out-of-order
delivery.
"""
from redux.mesh import ScopeSync, LoopbackMesh, SignedMessage, sign, verify
from redux.core import Scope


KEY = b"swarm-key"


def _node(name):
    return ScopeSync(Scope(), KEY, node_id=name)


# --- signing ----------------------------------------------------------------- #

def test_sign_verify_roundtrip_and_wrong_key_fails():
    p = {"op": "add", "kind": "bssid", "value": "aa:bb", "ts": 1.0}
    s = sign(p, KEY)
    assert verify(p, s, KEY) is True
    assert verify(p, s, b"other-key") is False
    assert verify(p, "deadbeef", KEY) is False


# --- convergence ------------------------------------------------------------- #

def test_arm_propagates_to_the_swarm():
    a, b, c = _node("a"), _node("b"), _node("c")
    mesh = LoopbackMesh()
    for n in (a, b, c):
        mesh.register(n)
    msg = a.arm("00:11:22:33:44:55", "bssid", job="op", now=100.0)
    mesh.broadcast(a, msg, now=100.0)
    assert b.scope.permits(bssid="00:11:22:33:44:55") is True
    assert c.scope.permits(bssid="00:11:22:33:44:55") is True


def test_unarm_propagates_removal():
    a, b = _node("a"), _node("b")
    mesh = LoopbackMesh(); mesh.register(a); mesh.register(b)
    mesh.broadcast(a, a.arm("CorpNet", "ssid", now=1.0), now=1.0)
    assert b.scope.permits(ssid="CorpNet") is True
    mesh.broadcast(a, a.unarm("CorpNet", "ssid", now=2.0), now=2.0)
    assert b.scope.permits(ssid="CorpNet") is False


def test_emit_full_brings_a_late_joiner_up_to_date():
    a = _node("a")
    a.arm("10.0.0.0/24", "cidr", now=1.0)
    a.arm("CorpNet", "ssid", now=2.0)
    late = _node("late")
    for msg in a.emit_full(now=5.0):
        ok, _ = late.apply(msg, now=5.0)
        assert ok
    assert late.scope.permits(ip="10.0.0.5") and late.scope.permits(ssid="CorpNet")


# --- forgery rejection (the headline) ---------------------------------------- #

def test_forged_delta_is_rejected_and_scope_untouched():
    b = _node("b")
    forged = SignedMessage(
        payload={"op": "add", "kind": "ssid", "value": "EvilTarget", "job": "",
                 "expires": None, "ts": 10.0, "origin": "attacker"},
        sig=sign({"op": "add"}, b"WRONG-KEY"))
    ok, reason = b.apply(forged, now=10.0)
    assert ok is False and "bad signature" in reason
    assert b.scope.permits(ssid="EvilTarget") is False
    assert b.rejected == 1


def test_tampered_payload_after_signing_is_rejected():
    a, b = _node("a"), _node("b")
    msg = a.arm("aa:bb:cc:dd:ee:ff", "bssid", now=1.0)
    tampered = SignedMessage(payload={**msg.payload, "value": "00:00:00:00:00:00"}, sig=msg.sig)
    ok, _ = b.apply(tampered, now=1.0)
    assert ok is False and b.scope.permits(bssid="00:00:00:00:00:00") is False


def test_malformed_delta_rejected():
    b = _node("b")
    p = {"op": "add"}   # missing fields
    ok, reason = b.apply(SignedMessage(payload=p, sig=sign(p, KEY)), now=1.0)
    assert ok is False and "malformed" in reason


# --- last-writer-wins convergence -------------------------------------------- #

def test_stale_remove_does_not_resurrect_or_delete():
    b = _node("b")
    add = _node("a").arm  # borrow a signer with the same key
    signer = ScopeSync(Scope(), KEY, "a")
    b.apply(signer.arm("CorpNet", "ssid", now=10.0), now=10.0)
    assert b.scope.permits(ssid="CorpNet")
    # a remove with an OLDER ts must be ignored (out-of-order delivery)
    stale = signer.unarm("CorpNet", "ssid", now=5.0)
    ok, reason = b.apply(stale, now=11.0)
    assert ok is False and "stale" in reason
    assert b.scope.permits(ssid="CorpNet") is True       # not removed by the stale delta


def test_global_remove_supersedes_and_stale_readd_cannot_resurrect():
    # clock is keyed on the target (not the job label), so a newer remove wins and
    # a stale re-add under the original job can't bring the target back
    b = _node("b")
    signer = ScopeSync(Scope(), KEY, "a")
    b.apply(signer.arm("CorpNet", "ssid", job="op1", now=10.0), now=10.0)
    assert b.scope.permits(ssid="CorpNet") is True
    b.apply(signer.unarm("CorpNet", "ssid", now=20.0), now=20.0)
    assert b.scope.permits(ssid="CorpNet") is False
    stale_readd = ScopeSync(Scope(), KEY, "c").arm("CorpNet", "ssid", job="op1", now=15.0)
    ok, reason = b.apply(stale_readd, now=21.0)
    assert ok is False and "stale" in reason
    assert b.scope.permits(ssid="CorpNet") is False


def test_newer_remove_wins():
    b = _node("b")
    signer = ScopeSync(Scope(), KEY, "a")
    b.apply(signer.arm("CorpNet", "ssid", now=10.0), now=10.0)
    b.apply(signer.unarm("CorpNet", "ssid", now=20.0), now=20.0)
    assert b.scope.permits(ssid="CorpNet") is False
