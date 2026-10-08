"""At-rest encryption — real AEAD, honest/fail-closed when the backend is absent."""
import pytest

from redux.vault import vault as V
from redux.vault import (
    Vault, VaultUnavailable, BadVaultData, crypto_available, derive_key, MAGIC,
)


def test_crypto_is_available_here():
    assert crypto_available() is True


def test_seal_unseal_roundtrip():
    v = Vault("correct horse battery staple")
    blob = v.seal(b"handshake:aa:bb:cc")
    assert blob.startswith(MAGIC) and b"handshake" not in blob   # ciphertext, not plaintext
    assert v.unseal(blob) == b"handshake:aa:bb:cc"


def test_salt_makes_each_seal_unique():
    v = Vault("pw")
    a, b = v.seal(b"x"), v.seal(b"x")
    assert a != b                                   # fresh random salt each time
    assert v.unseal(a) == v.unseal(b) == b"x"


def test_wrong_passphrase_is_rejected():
    blob = Vault("right").seal(b"secret")
    with pytest.raises(BadVaultData):
        Vault("wrong").unseal(blob)


def test_tampered_or_non_envelope_rejected():
    with pytest.raises(BadVaultData):
        Vault("pw").unseal(b"not an envelope")
    good = Vault("pw").seal(b"data")
    with pytest.raises(BadVaultData):
        Vault("pw").unseal(good[:-1] + bytes([good[-1] ^ 0x01]))   # flip a byte → HMAC fails


def test_empty_passphrase_refused():
    with pytest.raises(ValueError):
        Vault("")


def test_file_roundtrip(tmp_path):
    src = tmp_path / "loot.bin"
    src.write_bytes(b"\x00\x01PSK-material")
    sealed, out = tmp_path / "loot.vault", tmp_path / "loot.out"
    Vault("pw").seal_file(src, sealed)
    assert sealed.read_bytes().startswith(MAGIC) and b"PSK" not in sealed.read_bytes()
    Vault("pw").unseal_file(sealed, out)
    assert out.read_bytes() == b"\x00\x01PSK-material"


def test_derive_key_is_deterministic_and_passphrase_bound():
    salt = b"0123456789abcdef"
    assert derive_key("pw", salt) == derive_key("pw", salt)
    assert derive_key("pw", salt) != derive_key("other", salt)


def test_sealed_cache_export_roundtrips():
    from redux.geo.db import SightingStore, Sighting
    st = SightingStore(":memory:")
    st.insert(Sighting(kind="wifi", mac="aa:bb:cc:00:00:01", ssid="N", ts=1.0, provenance="t"))
    data = st.export_bytes(fmt="jsonl")
    v = Vault("pw")
    sealed = v.seal(data)
    assert b"aa:bb:cc" not in sealed and v.unseal(sealed) == data   # protected + recoverable


def test_honest_absence_fails_closed(monkeypatch):
    # with the backend gone, the vault refuses rather than writing plaintext
    monkeypatch.setattr(V, "_fernet_cls", lambda: None)
    assert V.crypto_available() is False
    with pytest.raises(VaultUnavailable):
        V.Vault("pw")
