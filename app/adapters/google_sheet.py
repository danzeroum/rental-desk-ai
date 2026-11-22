"""Planilha Google sincronizada (RF27): exporta a planilha modelo como .xlsx.

A conta de serviço (JSON em `GOOGLE_SERVICE_ACCOUNT_JSON`) só lê o que o gestor
compartilhou com o e-mail dela. Token OAuth por JWT assinado (RS256, `cryptography`),
guardado até vencer. Sem a variável, a ligação fica desligada (pendência humana).
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import httpx2
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

SCOPE: Final = "https://www.googleapis.com/auth/drive.readonly"
EXPORT_URL: Final = "https://www.googleapis.com/drive/v3/files/{id}/export"
XLSX: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_BYTES: Final = 10 * 1024 * 1024
TIMEOUT_SECONDS: Final = 30.0
_SHEET_ID: Final = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,100})|^([A-Za-z0-9_-]{20,100})$")


class SheetError(Exception):
    """Mensagem para o painel."""


def sheet_id_from(link: str) -> str:
    found = _SHEET_ID.search(link.strip())
    if found is None:
        raise SheetError("cole o link da planilha Google (docs.google.com/spreadsheets/d/...)")
    return found.group(1) or found.group(2)


class SheetSource(Protocol):
    service_email: str | None

    async def fetch_xlsx(self, sheet_id: str) -> bytes: ...


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class GoogleSheetSource:
    def __init__(
        self, service_account_json: str, *, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        try:
            info = json.loads(service_account_json)
            self.service_email: str | None = str(info["client_email"])
            self._token_uri = str(info.get("token_uri", "https://oauth2.googleapis.com/token"))
            key = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
        except (ValueError, KeyError, TypeError) as exc:
            raise SheetError("GOOGLE_SERVICE_ACCOUNT_JSON inválido") from exc
        if not isinstance(key, rsa.RSAPrivateKey):
            raise SheetError("a chave da conta de serviço precisa ser RSA")
        self._key, self._transport = key, transport
        self._token: tuple[str, float] | None = None

    def _assertion(self, now: float) -> str:
        header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = _b64(
            json.dumps(
                {"iss": self.service_email, "scope": SCOPE, "aud": self._token_uri,
                 "iat": int(now), "exp": int(now) + 3600}
            ).encode()
        )  # fmt: skip
        signature = self._key.sign(
            f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        return f"{header}.{claims}.{_b64(signature)}"

    async def _access_token(self, client: httpx2.AsyncClient) -> str:
        now = time.time()
        if self._token and self._token[1] - 60 > now:
            return self._token[0]
        response = await client.post(
            self._token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": self._assertion(now),
            },
        )
        if response.is_error:
            raise SheetError("a conta de serviço do Google foi recusada (chave revogada?)")
        body: dict[str, Any] = response.json()
        self._token = (str(body["access_token"]), now + float(body.get("expires_in", 3600)))
        return self._token[0]

    async def fetch_xlsx(self, sheet_id: str) -> bytes:
        try:
            async with httpx2.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS
            ) as client:
                token = await self._access_token(client)
                response = await client.get(
                    EXPORT_URL.format(id=sheet_id), params={"mimeType": XLSX},
                    headers={"Authorization": f"Bearer {token}"},
                )  # fmt: skip
        except httpx2.HTTPError as exc:
            raise SheetError("o Google não respondeu; tento de novo na próxima rodada") from exc
        if response.status_code in {403, 404}:
            raise SheetError(
                f"planilha não encontrada ou não compartilhada com {self.service_email}"
            )
        if response.is_error:
            raise SheetError(f"o Google respondeu com erro {response.status_code}")
        if len(response.content) > MAX_BYTES:
            raise SheetError("planilha grande demais (mais de 10 MB)")
        return response.content


@dataclass
class FakeSheetSource:
    sheets: dict[str, bytes | Exception] = field(default_factory=dict)
    service_email: str | None = "rental-desk@projeto-teste.iam.gserviceaccount.com"

    async def fetch_xlsx(self, sheet_id: str) -> bytes:
        sheet = self.sheets.get(
            sheet_id,
            SheetError(f"planilha não encontrada ou não compartilhada com {self.service_email}"),
        )
        if isinstance(sheet, Exception):
            raise sheet
        return sheet


class DisabledSheetSource:
    """Sem `GOOGLE_SERVICE_ACCOUNT_JSON`: a ligação com o Google fica desligada."""

    service_email: str | None = None

    async def fetch_xlsx(self, sheet_id: str) -> bytes:
        raise SheetError("ligação com o Google ainda não configurada pelo operador")
