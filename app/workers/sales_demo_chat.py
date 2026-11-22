"""Conversa do kit de demonstração para vendas: link com token, visita e as duas telas.

O visitante abre um link `/demo/entrar/<token>` criado pelo operador; o token vira uma
visita (cookie `rd_demo`, 4 h) e sai do endereço. Cada mensagem entra pelo mesmo caminho
do webhook (`ingest_batch` → debounce → worker → motor → cartões), num número `demo-…`
cujos envios o `DemoGateway` grava no Redis. Revogar o link encerra as visitas na hora:
toda requisição confere o link no banco.

O visitante ganha um telefone fictício (+55 009…, DDD que não existe). Nada do que ele
escreve é dado real, e a limpeza diária apaga contatos e conversas das imobiliárias demo.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.demo_gateway import DemoEntry, DemoOutbox
from app.adapters.whatsapp_webhook import InboundMessage, WebhookBatch
from app.core.logging import get_logger
from app.core.rls import global_session
from app.core.security import hash_token, new_token
from app.importer.sales_demo import SALES_DEMOS, DemoAgency
from app.models import DemoLink
from app.models.enums import MessageKind
from app.workers.ingest import ingest_batch
from app.workers.queue import WorkQueue

log = get_logger(__name__)

VISIT_TTL: Final = timedelta(hours=4)
VISIT_PREFIX: Final = "rd:demo:visit:"
MAX_TEXT: Final = 500
MAX_MESSAGES_PER_VISIT: Final = 80
TYPING_FOR: Final = timedelta(seconds=90)
"""Depois disso sem resposta, a tela para de mostrar "digitando…"."""
ASSUME_TEXT: Final = "1"
BROKER_SUFFIX: Final = "c"
"""Chave das mensagens que o "corretor" mandou nesta visita (só ela vê)."""
HANDOFF_TEMPLATE: Final = "passagem_corretor"

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DemoAccessError(Exception):
    """Mensagem pronta para a tela."""


@dataclass(frozen=True)
class Visit:
    key: str
    link_id: uuid.UUID
    wa_id: str
    """Telefone fictício do visitante, só dígitos (como o webhook entrega)."""
    sent: int

    @property
    def phone(self) -> str:
        return f"+{self.wa_id}"


@dataclass(frozen=True)
class DemoView:
    chat: list[DemoEntry]
    broker: list[DemoEntry]
    typing: bool
    can_assume: bool
    assumed: bool


def agency_by_pnid(pnid: str) -> DemoAgency | None:
    return next((a for a in SALES_DEMOS if a.pnid == pnid), None)


def fake_wa_id() -> str:
    return "55009" + "".join(secrets.choice("0123456789") for _ in range(8))


class SalesDemoChat:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        queue: WorkQueue,
        clock: Clock = _utcnow,
    ) -> None:
        self.sessions, self.redis, self.queue, self.clock = sessions, redis, queue, clock
        self.outbox = DemoOutbox(redis, clock)

    # --- Links (operador) -----------------------------------------------------------

    async def create_link(self, label: str, created_by: uuid.UUID) -> tuple[str, DemoLink]:
        label = " ".join(label.split())[:120]
        if not label:
            raise DemoAccessError("Dê um nome ao link (ex.: visita à Imobiliária X).")
        token = new_token()
        link = DemoLink(token_hash=hash_token(token), label=label, created_by=created_by)
        async with global_session(self.sessions, user_id=created_by) as session:
            session.add(link)
            await session.flush()
            await session.refresh(link)
        log.info("demo_link_criado", link_id=str(link.id))
        return token, link

    async def links(self) -> list[DemoLink]:
        async with global_session(self.sessions) as session:
            rows = await session.scalars(select(DemoLink).order_by(DemoLink.created_at.desc()))
            return list(rows)

    async def revoke(self, link_id: uuid.UUID, user_id: uuid.UUID) -> None:
        async with global_session(self.sessions, user_id=user_id) as session:
            await session.execute(
                update(DemoLink)
                .where(DemoLink.id == link_id, DemoLink.revoked_at.is_(None))
                .values(revoked_at=self.clock())
            )
        log.info("demo_link_revogado", link_id=str(link_id))

    # --- Visita ------------------------------------------------------------------------

    async def start_visit(self, token: str) -> str | None:
        """Token válido → nova visita; devolve o valor do cookie. Inválido/revogado → None."""
        now = self.clock()
        async with global_session(self.sessions) as session:
            link = await session.scalar(
                select(DemoLink).where(
                    DemoLink.token_hash == hash_token(token), DemoLink.revoked_at.is_(None)
                )
            )
            if link is None:
                return None
            link.last_used_at, link.visits = now, link.visits + 1
            link_id = link.id
        cookie = new_token()
        await self._save(Visit(VISIT_PREFIX + hash_token(cookie), link_id, fake_wa_id(), 0))
        log.info("demo_visita_iniciada", link_id=str(link_id))
        return cookie

    async def visit(self, cookie: str | None) -> Visit | None:
        if not cookie:
            return None
        key = VISIT_PREFIX + hash_token(cookie)
        raw = await self.redis.get(key)
        if raw is None:
            return None
        data = json.loads(raw)
        visit = Visit(key, uuid.UUID(data["link"]), str(data["wa"]), int(data["sent"]))
        async with global_session(self.sessions) as session:
            revoked = await session.scalar(
                select(DemoLink.revoked_at.is_not(None)).where(DemoLink.id == visit.link_id)
            )
        if revoked is None or revoked:
            await self.redis.delete(key)
            return None
        return visit

    async def _save(self, visit: Visit) -> None:
        payload = {"link": str(visit.link_id), "wa": visit.wa_id, "sent": visit.sent}
        await self.redis.set(visit.key, json.dumps(payload), ex=int(VISIT_TTL.total_seconds()))

    async def restart(self, visit: Visit) -> Visit:
        """Outro telefone fictício: a conversa recomeça do zero, como um hóspede novo."""
        fresh = Visit(visit.key, visit.link_id, fake_wa_id(), visit.sent)
        await self._save(fresh)
        return fresh

    # --- Conversa ----------------------------------------------------------------------

    async def say(self, visit: Visit, agency: DemoAgency, text: str) -> None:
        text = text.strip()[:MAX_TEXT]
        if not text:
            return
        if visit.sent >= MAX_MESSAGES_PER_VISIT:
            raise DemoAccessError("Limite de mensagens desta visita. Abra o link de novo.")
        await self._save(Visit(visit.key, visit.link_id, visit.wa_id, visit.sent + 1))
        await self.outbox.push(agency.pnid, visit.wa_id, text, inbound=True)
        await self._ingest(agency, visit.wa_id, text)

    async def broker_assume(self, visit: Visit, agency: DemoAgency) -> None:
        """O "corretor" responde 1 ao aviso, como faria no WhatsApp pessoal."""
        view = await self.view(visit, agency)
        if not view.can_assume:
            raise DemoAccessError("Ainda não há passagem para assumir nesta conversa.")
        await self.outbox.push(agency.pnid, visit.wa_id + BROKER_SUFFIX, ASSUME_TEXT, inbound=True)
        await self._ingest(agency, agency.broker_phone.removeprefix("+"), ASSUME_TEXT)

    async def _ingest(self, agency: DemoAgency, wa_id: str, text: str) -> None:
        message = InboundMessage(
            phone_number_id=agency.pnid, external_id=f"wamid.demo.in.{uuid.uuid4().hex}",
            wa_id=wa_id, kind=MessageKind.TEXT, timestamp=self.clock(), text=text,
        )  # fmt: skip
        result = await ingest_batch(
            WebhookBatch(messages=[message]), sessions=self.sessions, queue=self.queue,
            now=self.clock(),
        )  # fmt: skip
        if result.unknown_number:
            raise DemoAccessError(
                "A demonstração ainda não foi preparada neste servidor: rode make seed-demo."
            )

    async def view(self, visit: Visit, agency: DemoAgency) -> DemoView:
        chat = await self.outbox.read(agency.pnid, visit.wa_id)
        # O corretor fictício é um só: mostramos só os avisos que citam este visitante.
        notices = [
            e for e in await self.outbox.read(agency.pnid, agency.broker_phone)
            if visit.phone in e.text
        ]  # fmt: skip
        mine = await self.outbox.read(agency.pnid, visit.wa_id + BROKER_SUFFIX)
        broker = sorted([*notices, *mine], key=lambda e: e.at)
        last = chat[-1] if chat else None
        typing = bool(last and last.inbound and self.clock() - last.at < TYPING_FOR)
        handed = any(e.template == HANDOFF_TEMPLATE for e in notices)
        assumed = bool(mine)
        return DemoView(chat, broker, typing, handed and not assumed, assumed)
