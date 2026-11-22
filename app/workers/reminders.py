"""Lembrete diário das 9h ao gestor e leitura da resposta (RF16).

O lembrete sai por template (`lembrete_datas`) só para gestor com consentimento
`daily_reminder`. A resposta chega como mensagem de membro da equipe: "OK" renova a
validade das datas (RF13); "CASA12 28/12-02/01" cria bloqueio de origem `reminder`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, time
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.whatsapp import GatewayError, WhatsAppGateway
from app.core.audit import append_audit
from app.core.config import TIMEZONE
from app.core.crypto import Keyring
from app.core.logging import get_logger
from app.core.rls import tenant_session
from app.domain import notices
from app.domain.reminder import ReminderReply, parse_reply
from app.models import Block, Property, Tenant
from app.models.enums import BlockSource, ConsentPurpose
from app.workers.brokers import managers_with_consent, member_by_phone
from app.workers.channel import ChannelMissingError, channel_for
from app.workers.processor import MemberMessageHandler
from app.workers.responder import send_reply

SEND_FROM: Final = time(9, 0)
SEND_UNTIL: Final = time(18, 0)
"""Se o scheduler estava parado às 9h, o lembrete sai quando ele voltar, mas não à noite."""
log = get_logger(__name__)
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def reminder_due(now: datetime) -> bool:
    return SEND_FROM <= now.astimezone(TIMEZONE).time() < SEND_UNTIL


class ReminderService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        gateway: WhatsAppGateway,
        keyring: Keyring,
        public_base_url: str,
        clock: Clock = _utcnow,
    ) -> None:
        self.sessions, self.gateway, self.keyring = sessions, gateway, keyring
        self.panel_url = f"{public_base_url.rstrip('/')}/painel/bloqueios"
        self.clock = clock

    # --- Envio ---------------------------------------------------------------------

    async def send(self, tenant_id: uuid.UUID) -> int:
        """Envia o lembrete aos gestores com consentimento; devolve quantos receberam."""
        now = self.clock()
        today = now.astimezone(TIMEZONE).date()
        async with tenant_session(self.sessions, tenant_id) as session:
            tenant = await session.get(Tenant, tenant_id)
            recipients = await managers_with_consent(session, ConsentPurpose.DAILY_REMINDER)
            if tenant is None or not recipients:
                return 0
            try:
                channel = await channel_for(session, tenant_id, self.keyring)
            except ChannelMissingError:
                log.error("lembrete_sem_numero_conectado")
                return 0
            count = await session.scalar(
                select(func.count()).select_from(Block).where(Block.end_date > today)
            )
            params = [tenant.name, notices.reminder_count_text(int(count or 0)), self.panel_url]
        sent = []
        for person in recipients:
            try:
                await self.gateway.send_template(
                    channel,
                    person.phone_e164.removeprefix("+"),
                    notices.DAILY_REMINDER.name,
                    params,
                )
                sent.append(str(person.user_id))
            except GatewayError as exc:
                log.warning("lembrete_falhou", status=exc.status)
        async with tenant_session(self.sessions, tenant_id) as session:
            await append_audit(
                session, tenant_id=tenant_id, event_type="reminder_sent",
                payload={"user_ids": sent, "date": today.isoformat()},
            )  # fmt: skip
        return len(sent)

    # --- Resposta -------------------------------------------------------------------

    async def handle_member_message(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, phone_e164: str, text: str
    ) -> bool:
        """`True` se era resposta ao lembrete (e foi respondida)."""
        now = self.clock()
        async with tenant_session(self.sessions, tenant_id) as session:
            member = await member_by_phone(session, phone_e164)
            if member is None:
                return False
            tenant = await session.get(Tenant, tenant_id)
            codes = frozenset(
                (
                    await session.execute(
                        select(Property.code).where(
                            Property.snapshot_id == (tenant.active_snapshot_id if tenant else None)
                        )
                    )
                ).scalars()
            )
            user_id = member.user_id
        reply = parse_reply(text, today=now.astimezone(TIMEZONE).date(), codes=codes)
        if reply is None:
            return False
        answer = await self._apply(tenant_id, user_id, reply, now)
        await send_reply(
            self.sessions, self.gateway, self.keyring,
            tenant_id=tenant_id, conversation_id=conversation_id, text=answer,
            event_type="reminder_reply", audit_payload={"user_id": str(user_id)},
            snapshot_hash=None, now=now,
        )  # fmt: skip
        return True

    async def _apply(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, reply: ReminderReply, now: datetime
    ) -> str:
        async with tenant_session(self.sessions, tenant_id, user_id=user_id) as session:
            for block in reply.blocks:
                row = Block(
                    tenant_id=tenant_id, property_code=block.property_code,
                    start_date=block.start_date, end_date=block.end_date,
                    source=BlockSource.REMINDER, created_by=user_id,
                )  # fmt: skip
                session.add(row)
                await session.flush()
                await append_audit(
                    session, tenant_id=tenant_id, event_type="block_added",
                    payload={"block_id": str(row.id), "codigo": block.property_code,
                             "entrada": block.start_date.isoformat(),
                             "saida": block.end_date.isoformat(),
                             "user_id": str(user_id), "via": "reminder"},
                )  # fmt: skip
            if reply.confirm:
                tenant = await session.get(Tenant, tenant_id, with_for_update=True)
                if tenant is not None:
                    tenant.availability_updated_at = now
                await append_audit(
                    session, tenant_id=tenant_id, event_type="availability_confirmed",
                    payload={"user_id": str(user_id), "via": "reminder"},
                )  # fmt: skip
        return _answer(reply)


def _answer(reply: ReminderReply) -> str:
    parts = []
    if reply.blocks:
        listed = [(b.property_code, b.start_date, b.end_date) for b in reply.blocks]
        parts.append(notices.REMINDER_BLOCKED.format(blocks=notices.blocks_text(listed)))
        if not reply.confirm:
            parts[-1] += notices.REMINDER_ASK_OK
    if reply.problems:
        parts.append(notices.REMINDER_PROBLEMS.format(problems="; ".join(reply.problems)))
    if reply.confirm:
        parts.append(notices.REMINDER_CONFIRMED)
    return " ".join(parts)


class MemberCommands:
    """Mensagem de corretor/gestor: primeiro resposta ao lembrete, depois passagem ("1")."""

    def __init__(self, reminders: ReminderService, handoffs: MemberMessageHandler) -> None:
        self.reminders, self.handoffs = reminders, handoffs

    async def handle_member_message(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, phone_e164: str, text: str
    ) -> bool:
        if await self.reminders.handle_member_message(tenant_id, conversation_id, phone_e164, text):
            return True
        return await self.handoffs.handle_member_message(
            tenant_id, conversation_id, phone_e164, text
        )
