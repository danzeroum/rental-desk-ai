"""Gateway do kit de demonstração: nada sai para o WhatsApp.

Números com `phone_number_id` começando por `demo-` (só existem nas imobiliárias de
demonstração) têm os envios gravados no Redis, por telefone de destino, para a tela da
demo mostrar. Templates aparecem com o texto aprovado (`notices.TEMPLATES`) já preenchido.
Os demais números seguem para o gateway real, sem mudança.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from redis.asyncio import Redis

from app.adapters.whatsapp import Channel, GatewayError, Media, WhatsAppGateway, template_param
from app.domain.notices import TEMPLATES

DEMO_PREFIX: Final = "demo-"
KEY_PREFIX: Final = "rd:demo:chat:"
TTL_SECONDS: Final = 26 * 3600
"""Mais que um dia: a limpeza diária apaga antes; a expiração é a rede de segurança."""
MAX_ENTRIES: Final = 300
_PARAM: Final = re.compile(r"\{\{(\d+)\}\}")
_BODIES: Final = {t.name: t.body for t in TEMPLATES}

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def is_demo_channel(phone_number_id: str) -> bool:
    return phone_number_id.startswith(DEMO_PREFIX)


def chat_key(phone_number_id: str, wa_id: str) -> str:
    return f"{KEY_PREFIX}{phone_number_id}:{wa_id.removeprefix('+')}"


def fill_template(name: str, params: Sequence[str]) -> str:
    body = _BODIES.get(name)
    if body is None:
        return " | ".join(params)

    def value(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        return params[index] if 0 <= index < len(params) else match.group(0)

    return _PARAM.sub(value, body)


@dataclass(frozen=True)
class DemoEntry:
    inbound: bool
    text: str
    at: datetime
    template: str | None = None


class DemoOutbox:
    """Conversa da demonstração no Redis: o que o visitante escreveu e o que o produto enviou."""

    def __init__(self, redis: Redis, clock: Clock = _utcnow) -> None:
        self.redis, self.clock = redis, clock

    async def push(
        self, phone_number_id: str, wa_id: str, text: str, *, inbound: bool,
        template: str | None = None,
    ) -> None:  # fmt: skip
        key = chat_key(phone_number_id, wa_id)
        entry = {"in": inbound, "text": text, "at": self.clock().isoformat(), "t": template}
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.rpush(key, json.dumps(entry, ensure_ascii=False))
            pipe.ltrim(key, -MAX_ENTRIES, -1)
            pipe.expire(key, TTL_SECONDS)
            await pipe.execute()

    async def read(self, phone_number_id: str, wa_id: str) -> list[DemoEntry]:
        raw = await self.redis.lrange(chat_key(phone_number_id, wa_id), 0, -1)
        return [_entry(json.loads(item)) for item in raw]

    async def clear_all(self) -> int:
        deleted = 0
        async for key in self.redis.scan_iter(match=f"{KEY_PREFIX}*", count=500):
            deleted += int(await self.redis.delete(key))
        return deleted


def _entry(data: dict[str, Any]) -> DemoEntry:
    return DemoEntry(bool(data["in"]), str(data["text"]), datetime.fromisoformat(data["at"]),
                     data.get("t"))  # fmt: skip


class DemoGateway:
    def __init__(self, outbox: DemoOutbox) -> None:
        self.outbox = outbox

    async def send_text(self, channel: Channel, to: str, body: str) -> str:
        await self.outbox.push(channel.phone_number_id, to, body, inbound=False)
        return f"wamid.demo.{uuid.uuid4().hex}"

    async def send_template(
        self, channel: Channel, to: str, template: str, params: Sequence[str],
        language: str = "pt_BR",
    ) -> str:  # fmt: skip
        clean = [template_param(p) for p in params]
        text = fill_template(template, clean)
        await self.outbox.push(channel.phone_number_id, to, text, inbound=False, template=template)
        return f"wamid.demo.{uuid.uuid4().hex}"

    async def fetch_media(self, channel: Channel, media_id: str) -> Media:
        raise GatewayError("a demonstração não recebe mídia", retryable=False, status=404)

    async def phone_quality(self, channel: Channel) -> str:
        return "GREEN"


class RoutingGateway:
    """Envia pelo gateway de demonstração os números `demo-` e pelo real os demais."""

    def __init__(self, real: WhatsAppGateway, demo: WhatsAppGateway) -> None:
        self.real, self.demo = real, demo

    def _pick(self, channel: Channel) -> WhatsAppGateway:
        return self.demo if is_demo_channel(channel.phone_number_id) else self.real

    async def send_text(self, channel: Channel, to: str, body: str) -> str:
        return await self._pick(channel).send_text(channel, to, body)

    async def send_template(
        self, channel: Channel, to: str, template: str, params: Sequence[str],
        language: str = "pt_BR",
    ) -> str:  # fmt: skip
        return await self._pick(channel).send_template(channel, to, template, params, language)

    async def fetch_media(self, channel: Channel, media_id: str) -> Media:
        return await self._pick(channel).fetch_media(channel, media_id)

    async def phone_quality(self, channel: Channel) -> str:
        return await self._pick(channel).phone_quality(channel)
