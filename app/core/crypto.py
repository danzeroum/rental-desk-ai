"""Cifra de segredos guardados no banco (tokens de terceiros, segredo TOTP).

AES-256-GCM com chave vinda do ambiente (`DATA_ENCRYPTION_KEYS`). Formato:
`<id da chave>:<nonce 12 bytes><texto cifrado+tag>`. O `context` (ex.:
"whatsapp_token:<tenant_id>") entra como dado associado: um valor cifrado copiado
para outra linha ou outro tenant não decifra.

Rotação: `DATA_ENCRYPTION_KEYS=k2:<base64>,k1:<base64>` — cifra com a primeira,
decifra com qualquer uma.
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12
MAX_KEY_ID_LENGTH: Final = 16


class CryptoError(Exception):
    """Configuração de chave inválida ou valor que não decifra."""


@dataclass(frozen=True)
class Keyring:
    current_id: str
    keys: dict[str, bytes]

    @classmethod
    def parse(cls, spec: str) -> Keyring:
        keys: dict[str, bytes] = {}
        order: list[str] = []
        for item in (part.strip() for part in spec.split(",") if part.strip()):
            key_id, sep, encoded = item.partition(":")
            if not sep or not key_id.isalnum() or len(key_id) > MAX_KEY_ID_LENGTH:
                raise CryptoError("DATA_ENCRYPTION_KEYS: use id:base64, ex.: k1:AAAA...")
            try:
                key = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise CryptoError(f"DATA_ENCRYPTION_KEYS: chave '{key_id}' não é base64") from None
            if len(key) != KEY_BYTES:
                raise CryptoError(f"DATA_ENCRYPTION_KEYS: chave '{key_id}' precisa de 32 bytes")
            keys[key_id] = key
            order.append(key_id)
        if not order:
            raise CryptoError("DATA_ENCRYPTION_KEYS vazia")
        return cls(current_id=order[0], keys=keys)

    def encrypt(self, plaintext: str, *, context: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        sealed = AESGCM(self.keys[self.current_id]).encrypt(
            nonce, plaintext.encode(), context.encode()
        )
        return self.current_id.encode() + b":" + nonce + sealed

    def decrypt(self, token: bytes, *, context: str) -> str:
        key_id, sep, rest = token.partition(b":")
        key = self.keys.get(key_id.decode(errors="replace"))
        if not sep or key is None or len(rest) <= NONCE_BYTES:
            raise CryptoError("valor cifrado com chave desconhecida ou corrompido")
        try:
            plain = AESGCM(key).decrypt(rest[:NONCE_BYTES], rest[NONCE_BYTES:], context.encode())
        except InvalidTag:
            raise CryptoError("valor cifrado não confere (contexto ou chave errados)") from None
        return plain.decode()


def generate_key_spec(key_id: str = "k1") -> str:
    return f"{key_id}:{base64.b64encode(os.urandom(KEY_BYTES)).decode()}"
