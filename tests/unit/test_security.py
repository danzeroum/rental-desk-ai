from datetime import UTC, datetime

import pytest

from app.core.security import (
    hash_password,
    hash_token,
    new_token,
    new_totp_secret,
    password_problem,
    provisioning_uri,
    qr_svg,
    totp_code,
    verify_password,
    verify_totp,
)
from app.web.common import local_time

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890" em base32


@pytest.mark.parametrize(
    ("unix", "code"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
    ],
)
def test_totp_matches_rfc_6238_vectors(unix: int, code: str) -> None:
    at = datetime.fromtimestamp(unix, tz=UTC)
    assert totp_code(RFC_SECRET, at, digits=8) == code


def test_verify_accepts_one_step_of_drift_and_returns_step() -> None:
    secret = new_totp_secret()
    now = datetime(2026, 11, 23, 10, 0, 15, tzinfo=UTC)
    previous = datetime.fromtimestamp(now.timestamp() - 30, tz=UTC)
    assert verify_totp(secret, totp_code(secret, now), now) == int(now.timestamp()) // 30
    assert verify_totp(secret, totp_code(secret, previous), now) is not None
    too_old = datetime.fromtimestamp(now.timestamp() - 90, tz=UTC)
    assert verify_totp(secret, totp_code(secret, too_old), now) is None


@pytest.mark.parametrize("bad", ["", "12345", "abcdef", "1234567"])
def test_verify_rejects_malformed(bad: str) -> None:
    assert verify_totp(new_totp_secret(), bad, datetime.now(UTC)) is None


def test_verify_ignores_spaces() -> None:
    secret, now = new_totp_secret(), datetime(2026, 1, 1, tzinfo=UTC)
    code = totp_code(secret, now)
    assert verify_totp(secret, f"{code[:3]} {code[3:]}", now) is not None


def test_password_hash_round_trip() -> None:
    hashed = hash_password("uma senha bem longa")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "uma senha bem longa")
    assert not verify_password(hashed, "outra senha qualquer")
    assert not verify_password("lixo", "uma senha bem longa")


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("curta", "pelo menos 12"),
        ("ana.souza-2026!", "parte do e-mail"),
        ("123456789012", "comum demais"),
        ("aaaaaaaaaaaa", "comum demais"),
    ],
)
def test_password_policy(password: str, problem: str) -> None:
    result = password_problem(password, email="ana.souza@exemplo.com")
    assert result is not None and problem in result


def test_good_password() -> None:
    assert password_problem("girassol azul na varanda", email="ana@x.com") is None


def test_provisioning_uri_and_qr() -> None:
    uri = provisioning_uri("JBSWY3DPEHPK3PXP", "ana@x.com", issuer="Rental Desk")
    assert uri.startswith("otpauth://totp/Rental%20Desk:ana%40x.com?secret=JBSWY3DPEHPK3PXP")
    assert "issuer=Rental%20Desk" in uri
    svg = qr_svg(uri)
    assert svg.lstrip().startswith("<svg")
    # Escalável sem cortar (o CSS define o tamanho) e com fundo branco também no modo escuro.
    head = svg.split(">", 1)[0]
    assert 'viewBox="0 0 ' in head and "width=" not in head
    assert 'fill="#fff"' in svg


def test_tokens_are_random_and_hashed() -> None:
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 40
    assert hash_token(a) == hash_token(a) and len(hash_token(a)) == 64


def test_local_time_filter_uses_sao_paulo() -> None:
    assert local_time(datetime(2026, 11, 22, 5, 31, tzinfo=UTC)) == "22/11/2026 02:31"
    assert local_time(datetime(2026, 11, 22, 2, 0, tzinfo=UTC), "%d/%m/%Y") == "21/11/2026"
    assert local_time(None) == "—"
