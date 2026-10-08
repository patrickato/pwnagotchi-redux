"""Swarm-key lifecycle — generate / rotate / expiry / exchange / sealed keystore."""
import pytest

from redux.mesh import (
    SwarmKey, SwarmKeyring, generate_key, export_token, import_token,
    scopesync_keys, save, load, ScopeSync,
)
from redux.core import Scope
from redux.vault import BadVaultData


def test_generate_key_is_random_with_stable_fingerprint():
    a, b = generate_key(now=100.0), generate_key(now=100.0)
    assert a.key != b.key and len(a.key) == 32          # fresh random each time
    assert a.kid == SwarmKey(a.key, 0.0).kid and len(a.kid) == 12   # kid = key fingerprint only


def test_key_expiry():
    k = generate_key(ttl=3600, now=100.0)
    assert not k.is_expired(100.0) and k.is_expired(3700.0)
    assert generate_key(now=100.0).is_expired(1e12) is False        # no ttl → never expires


def test_rotate_keeps_grace_and_bounds_history():
    r = SwarmKeyring(grace=2)
    k1 = r.rotate(now=1.0); r.rotate(now=2.0); r.rotate(now=3.0); k4 = r.rotate(now=4.0)
    assert r.active(now=5.0).kid == k4.kid               # newest is current
    assert len(r.keys) == 3                              # current + grace(2)
    assert k1.kid not in {k.kid for k in r.keys}         # oldest aged out


def test_active_skips_expired():
    r = SwarmKeyring()
    r.rotate(ttl=10, now=0.0)
    new = r.rotate(now=100.0)
    assert r.active(now=100.0).kid == new.kid            # the expired one is skipped
    r2 = SwarmKeyring(); r2.rotate(ttl=10, now=0.0)
    assert r2.active(now=100.0) is None                  # only expired → no active key


def test_exchange_token_roundtrip():
    k = generate_key(label="op1", ttl=3600, now=100.0)
    tok = export_token(k)
    assert tok.startswith("AKEY1:")
    k2 = import_token(tok)
    assert k2.key == k.key and k2.label == "op1" and k2.expires == k.expires


def test_import_token_rejects_garbage():
    with pytest.raises(ValueError):
        import_token("not-a-token")
    with pytest.raises(ValueError):
        import_token("AKEY1:@@@not-base64@@@")


def test_rotation_grace_lets_recent_keys_still_verify():
    r = SwarmKeyring()
    old = r.rotate(now=0.0)
    # a peer signs a delta with the OLD key before it hears about the rotation
    msg = ScopeSync(Scope(), old.key, node_id="peer").arm("00:11:22:33:44:55", "bssid", now=10.0)
    r.rotate(now=20.0)                                   # new current; old kept for grace
    sign_key, extras = scopesync_keys(r)
    me = ScopeSync(Scope(), sign_key, node_id="me", verify_keys=extras)
    ok, _ = me.apply(msg, now=25.0)
    assert ok and me.scope.permits(bssid="00:11:22:33:44:55")   # old-signed delta still trusted
    # but a key not in the ring is still rejected — grace isn't a free-for-all
    forged = ScopeSync(Scope(), b"WRONG", node_id="x").arm("aa:aa:aa:aa:aa:aa", "bssid", now=26.0)
    ok2, _ = me.apply(forged, now=27.0)
    assert not ok2


def test_scopesync_keys_errors_when_no_active_key():
    r = SwarmKeyring(); r.rotate(ttl=10, now=0.0)
    with pytest.raises(ValueError):
        scopesync_keys(r, now=1000.0)                    # all expired


def test_sealed_keystore_roundtrip(tmp_path):
    r = SwarmKeyring(); r.rotate(label="lab", now=1.0)
    path = tmp_path / "swarm.keys"
    save(r, path, "pass")
    assert path.read_bytes().startswith(b"AUGURv1")      # sealed at rest, not plaintext
    r2 = load(path, "pass")
    assert r2.keys[0].key == r.keys[0].key and r2.keys[0].label == "lab"


def test_sealed_keystore_wrong_passphrase(tmp_path):
    r = SwarmKeyring(); r.rotate(now=1.0)
    path = tmp_path / "swarm.keys"
    save(r, path, "right")
    with pytest.raises(BadVaultData):
        load(path, "wrong")
