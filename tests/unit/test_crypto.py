import pytest

from app.core.crypto import CryptoError, Keyring, generate_key_spec


@pytest.fixture
def ring() -> Keyring:
    return Keyring.parse(generate_key_spec("k1"))


def test_round_trip(ring: Keyring) -> None:
    sealed = ring.encrypt("token-secreto", context="whatsapp_token:t1")
    assert b"token-secreto" not in sealed
    assert ring.decrypt(sealed, context="whatsapp_token:t1") == "token-secreto"


def test_nonce_makes_each_ciphertext_different(ring: Keyring) -> None:
    assert ring.encrypt("x", context="c") != ring.encrypt("x", context="c")


def test_wrong_context_fails(ring: Keyring) -> None:
    sealed = ring.encrypt("token", context="whatsapp_token:t1")
    with pytest.raises(CryptoError, match="não confere"):
        ring.decrypt(sealed, context="whatsapp_token:t2")


def test_tampering_fails(ring: Keyring) -> None:
    sealed = bytearray(ring.encrypt("token", context="c"))
    sealed[-1] ^= 1
    with pytest.raises(CryptoError):
        ring.decrypt(bytes(sealed), context="c")


def test_rotation_decrypts_with_old_key() -> None:
    old = generate_key_spec("k1")
    sealed = Keyring.parse(old).encrypt("token", context="c")
    rotated = Keyring.parse(f"{generate_key_spec('k2')},{old}")
    assert rotated.current_id == "k2"
    assert rotated.decrypt(sealed, context="c") == "token"
    assert rotated.encrypt("token", context="c").startswith(b"k2:")


def test_unknown_key_id() -> None:
    sealed = Keyring.parse(generate_key_spec("k1")).encrypt("t", context="c")
    with pytest.raises(CryptoError, match="desconhecida"):
        Keyring.parse(generate_key_spec("k9")).decrypt(sealed, context="c")


@pytest.mark.parametrize(
    "spec", ["", "semdoispontos", "k1:não-base64!", "k1:AAAA", "k-1:" + "A" * 44]
)
def test_invalid_specs(spec: str) -> None:
    with pytest.raises(CryptoError):
        Keyring.parse(spec)
