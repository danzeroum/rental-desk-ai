"""Gateway do WhatsApp no formato da Cloud API (SPEC, seção 3).

Funciona com a Meta direta ou com parceiro oficial (BSP) compatível: a URL base,
o nome do cabeçalho de autenticação e o prefixo do token vêm da configuração.
Proibido qualquer acesso não oficial (WhatsApp Web, QR code e similares).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

import httpx2

from app.core.logging import get_logger

MAX_ATTEMPTS: Final = 3
MAX_MEDIA_BYTES: Final = 16 * 1024 * 1024  # limite de áudio do WhatsApp
TIMEOUT_SECONDS: Final = 15.0
MAX_TEMPLATE_PARAM: Final = 1000
RETRYABLE_STATUS: Final = frozenset({429, 500, 502, 503, 504})

log = get_logger(__name__)

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Channel:
    """Número da imobiliária na API e a credencial dele (já decifrada)."""

    phone_number_id: str
    access_token: str = field(repr=False)


@dataclass(frozen=True)
class Media:
    content: bytes = field(repr=False)
    mime_type: str


class GatewayError(Exception):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class WhatsAppGateway(Protocol):
    async def send_text(self, channel: Channel, to: str, body: str) -> str:
        """Envia texto; devolve o ID externo (wamid) da mensagem."""
        ...

    async def send_template(
        self,
        channel: Channel,
        to: str,
        template: str,
        params: Sequence[str],
        language: str = "pt_BR",
    ) -> str:
        """Mensagem ativa (fora da janela de 24 h) por template aprovado na Meta."""
        ...

    async def fetch_media(self, channel: Channel, media_id: str) -> Media: ...

    async def phone_quality(self, channel: Channel) -> str:
        """Qualidade do número na Meta: GREEN, YELLOW, RED ou UNKNOWN (RF23)."""
        ...


def template_param(value: str) -> str:
    """A Meta recusa quebras de linha, tabulações e 4+ espaços seguidos nos parâmetros."""
    flat = " · ".join(part.strip() for part in value.splitlines() if part.strip())
    return " ".join(flat.replace("\t", " ").split())[:MAX_TEMPLATE_PARAM]


class CloudApiGateway:
    def __init__(
        self,
        *,
        base_url: str,
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
        transport: httpx2.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_header = auth_header
        self._auth_scheme = auth_scheme.strip()
        self._transport = transport
        self._sleep = sleep

    def _headers(self, channel: Channel) -> dict[str, str]:
        value = (
            f"{self._auth_scheme} {channel.access_token}"
            if self._auth_scheme
            else channel.access_token
        )
        return {self._auth_header: value}

    def _client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=self._transport, timeout=TIMEOUT_SECONDS)

    async def _request(
        self, method: str, url: str, channel: Channel, *, json: dict[str, Any] | None = None
    ) -> httpx2.Response:
        delay = 0.5
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with self._client() as client:
                    response = await client.request(
                        method, url, json=json, headers=self._headers(channel)
                    )
            except httpx2.TransportError as exc:
                error = GatewayError(f"falha de rede: {type(exc).__name__}", retryable=True)
            else:
                if response.status_code < 400:  # noqa: PLR2004
                    return response
                error = GatewayError(
                    f"API respondeu {response.status_code}",
                    retryable=response.status_code in RETRYABLE_STATUS,
                    status=response.status_code,
                )
            log.warning(
                "whatsapp_api_erro", attempt=attempt, status=error.status, retryable=error.retryable
            )
            if not error.retryable or attempt == MAX_ATTEMPTS:
                raise error
            await self._sleep(delay)
            delay *= 2
        raise GatewayError("tentativas esgotadas", retryable=True)  # pragma: no cover

    async def send_text(self, channel: Channel, to: str, body: str) -> str:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        response = await self._request(
            "POST", f"{self._base_url}/{channel.phone_number_id}/messages", channel, json=payload
        )
        return self._message_id(response)

    async def send_template(
        self,
        channel: Channel,
        to: str,
        template: str,
        params: Sequence[str],
        language: str = "pt_BR",
    ) -> str:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": template_param(p)} for p in params],
                    }
                ],
            },
        }
        response = await self._request(
            "POST", f"{self._base_url}/{channel.phone_number_id}/messages", channel, json=payload
        )
        return self._message_id(response)

    @staticmethod
    def _message_id(response: httpx2.Response) -> str:
        try:
            message_id: str = response.json()["messages"][0]["id"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise GatewayError("resposta da API sem ID da mensagem", retryable=False) from None
        return message_id

    async def phone_quality(self, channel: Channel) -> str:
        response = await self._request(
            "GET", f"{self._base_url}/{channel.phone_number_id}?fields=quality_rating", channel
        )
        try:
            rating = response.json().get("quality_rating")
        except (ValueError, AttributeError):
            rating = None
        return rating.upper()[:20] if isinstance(rating, str) and rating else "UNKNOWN"

    async def fetch_media(self, channel: Channel, media_id: str) -> Media:
        meta = await self._request("GET", f"{self._base_url}/{media_id}", channel)
        try:
            info = meta.json()
            url, mime_type = (
                str(info["url"]),
                str(info.get("mime_type", "application/octet-stream")),
            )
        except (ValueError, KeyError, TypeError):
            raise GatewayError("resposta da API sem URL da mídia", retryable=False) from None
        if urlsplit(url).scheme != "https":
            raise GatewayError("URL de mídia recusada (precisa ser https)", retryable=False)
        if int(info.get("file_size") or 0) > MAX_MEDIA_BYTES:
            raise GatewayError("mídia grande demais", retryable=False)
        response = await self._request("GET", url, channel)
        if len(response.content) > MAX_MEDIA_BYTES:
            raise GatewayError("mídia grande demais", retryable=False)
        return Media(content=response.content, mime_type=mime_type)


@dataclass
class SentMessage:
    channel: Channel
    to: str
    body: str
    external_id: str
    template: str | None = None
    params: tuple[str, ...] = ()


class FakeWhatsAppGateway:
    """Grava os envios em memória; usado nos testes, no conjunto de ouro e em desenvolvimento."""

    def __init__(
        self, media: dict[str, Media] | None = None, *, fail_with: GatewayError | None = None
    ) -> None:
        self.sent: list[SentMessage] = []
        self.media = media or {}
        self.fail_with = fail_with
        self.quality = "GREEN"

    async def phone_quality(self, channel: Channel) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        return self.quality

    async def send_text(self, channel: Channel, to: str, body: str) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        external_id = f"wamid.fake.{len(self.sent) + 1}"
        self.sent.append(SentMessage(channel, to, body, external_id))
        return external_id

    async def send_template(
        self,
        channel: Channel,
        to: str,
        template: str,
        params: Sequence[str],
        language: str = "pt_BR",
    ) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        external_id = f"wamid.fake.{len(self.sent) + 1}"
        clean = tuple(template_param(p) for p in params)
        self.sent.append(SentMessage(channel, to, " | ".join(clean), external_id, template, clean))
        return external_id

    @property
    def templates(self) -> list[SentMessage]:
        return [m for m in self.sent if m.template is not None]

    @property
    def texts(self) -> list[SentMessage]:
        return [m for m in self.sent if m.template is None]

    async def fetch_media(self, channel: Channel, media_id: str) -> Media:
        if media_id not in self.media:
            raise GatewayError("mídia não encontrada", retryable=False, status=404)
        return self.media[media_id]
