"""Senha, TOTP e tokens do painel (SPEC, seção 5: e-mail + senha + TOTP para todos).

- Senha: argon2id (`argon2-cffi`), política mínima de tamanho e de obviedade.
- TOTP: RFC 6238 (HMAC-SHA1, 30 s, 6 dígitos), tolerância de um passo; o segredo
  fica cifrado no banco (`app/core/crypto.py`) e o passo usado é guardado para não
  aceitar o mesmo código duas vezes.
- Tokens (sessão, convite, recuperação): aleatórios; no servidor só o SHA-256.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import re
import secrets
import struct
from datetime import datetime
from typing import Final
from urllib.parse import quote, urlencode

import segno
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH: Final = 12
TOTP_STEP_SECONDS: Final = 30
TOTP_DIGITS: Final = 6
_COMMON: Final = frozenset(
    {"123456789012", "1234567890123", "senha1234567", "password1234", "qwertyuiopas"}
)
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def password_problem(password: str, *, email: str) -> str | None:
    """Mensagem para a pessoa, ou `None` se a senha serve."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"use pelo menos {MIN_PASSWORD_LENGTH} caracteres (uma frase curta funciona bem)"
    lowered = password.lower()
    if lowered in _COMMON or len(set(lowered)) <= 2:  # noqa: PLR2004
        return "senha comum demais; escolha outra"
    local = email.split("@", 1)[0].lower()
    if len(local) >= 4 and local in lowered:  # noqa: PLR2004
        return "a senha não pode conter parte do e-mail"
    return None


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()


def totp_code(secret: str, at: datetime, *, digits: int = TOTP_DIGITS) -> str:
    return _code_for_step(secret, int(at.timestamp()) // TOTP_STEP_SECONDS, digits)


def _code_for_step(secret: str, step: int, digits: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def verify_totp(secret: str, code: str, at: datetime, *, window: int = 1) -> int | None:
    """Passo de tempo aceito (para bloquear reuso) ou `None`."""
    cleaned = code.replace(" ", "")
    if not re.fullmatch(rf"\d{{{TOTP_DIGITS}}}", cleaned):
        return None
    current = int(at.timestamp()) // TOTP_STEP_SECONDS
    for step in range(current - window, current + window + 1):
        if hmac.compare_digest(_code_for_step(secret, step, TOTP_DIGITS), cleaned):
            return step
    return None


def provisioning_uri(secret: str, email: str, *, issuer: str) -> str:
    label = quote(f"{issuer}:{email}", safe=":")
    query = urlencode({"secret": secret, "issuer": issuer}, quote_via=quote)
    return f"otpauth://totp/{label}?{query}"


def qr_svg(data: str) -> str:
    buffer = io.BytesIO()
    segno.make(data, error="m").save(buffer, kind="svg", scale=5, xmldecl=False)
    return buffer.getvalue().decode()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
