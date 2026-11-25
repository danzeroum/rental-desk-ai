"""Retenção diária: nome e telefone saem das conversas paradas (SPEC, seção 9).

Roda no scheduler uma vez por dia, de madrugada (horário de Brasília), imobiliária por
imobiliária, com o papel da aplicação (RLS). Para cada hóspede sem conversa há mais de
`RETENTION_DAYS` e sem passagem ao corretor em aberto:

- o texto das mensagens perde telefone e nome do perfil e passa pela máscara de dados pessoais;
- menções ao telefone em qualquer mensagem da imobiliária (ex.: aviso ao corretor) também saem;
- o contato perde nome do perfil e telefone e ganha `anonymized_at`.

Exceção: quem pediu "PARAR" mantém o telefone, só ele, para o pedido continuar valendo (sem o
número, uma nova mensagem voltaria a receber atendimento automático). Decisão D048.

A trilha recebe `retention_applied` com as contagens, sem telefone.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from redis.asyncio import Redis
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.audit import append_audit
from app.core.config import TIMEZONE
from app.core.logging import get_logger
from app.core.masking import mask_pii
from app.core.rls import tenant_session
from app.domain.retention import ANONYMIZED_MARK, RetentionResult, cutoff, scrub
from app.models import Contact, Conversation, Handoff, Message
from app.models.enums import HandoffStatus

log = get_logger(__name__)

RETENTION_HOUR: Final = 4
"""Depois da limpeza da demonstração (3h), antes do lembrete das 9h."""
BATCH: Final = 500
"""Contatos por imobiliária por dia: limita a transação; o resto sai nos dias seguintes."""
RETENTION_CHECK_SECONDS: Final = 900


def retention_due(now: datetime) -> bool:
    return now.astimezone(TIMEZONE).hour >= RETENTION_HOUR


async def run_retention(
    redis: Redis, ops_engine: AsyncEngine, app: async_sessionmaker[AsyncSession], *,
    now: datetime, days: int,
) -> int:  # fmt: skip
    """Uma vez por dia (chave no Redis): aplica a retenção em todas as imobiliárias."""
    if not retention_due(now):
        return 0
    day = now.astimezone(TIMEZONE).date().isoformat()
    if not await redis.set(f"rd:retention:{day}", "1", nx=True, ex=2 * 86400):
        return 0
    total = RetentionResult()
    for tenant_id in await _tenants(ops_engine):
        result = await anonymize_tenant(app, tenant_id, now=now, days=days)
        total = RetentionResult(total.contacts + result.contacts, total.messages + result.messages)
    log.info("retencao_aplicada", contatos=total.contacts, mensagens=total.messages, dias=days)
    return total.contacts


async def _tenants(ops_engine: AsyncEngine) -> list[uuid.UUID]:
    """Todas menos as de demonstração (essas são limpas por inteiro toda madrugada)."""
    async with ops_engine.connect() as conn:
        rows = await conn.execute(text("SELECT id FROM tenant WHERE NOT is_demo"))
    return [row[0] for row in rows]


async def anonymize_tenant(
    app: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, *, now: datetime, days: int
) -> RetentionResult:
    async with tenant_session(app, tenant_id) as session:
        contacts = await _expired_contacts(session, cutoff(now, days))
        messages = 0
        for contact in contacts:
            messages += await _anonymize(session, contact, now)
        if contacts:
            await append_audit(
                session, tenant_id=tenant_id, event_type="retention_applied",
                payload={"contacts": len(contacts), "messages": messages, "days": days},
            )  # fmt: skip
    return RetentionResult(len(contacts), messages)


async def _expired_contacts(session: AsyncSession, limit: datetime) -> list[Contact]:
    last = (
        select(Conversation.contact_id, func.max(Message.created_at).label("last_at"))
        .join(Message, Message.conversation_id == Conversation.id)
        .group_by(Conversation.contact_id)
        .subquery()
    )
    pending = (
        select(Conversation.contact_id)
        .join(Handoff, Handoff.conversation_id == Conversation.id)
        .where(Handoff.status.in_((HandoffStatus.OPEN.value, HandoffStatus.ASSUMED.value)))
    )
    rows = await session.scalars(
        select(Contact)
        .outerjoin(last, last.c.contact_id == Contact.id)
        .where(
            Contact.anonymized_at.is_(None),
            func.coalesce(last.c.last_at, Contact.created_at) < limit,
            Contact.id.not_in(pending),
        )
        .limit(BATCH)
    )
    return list(rows)


async def _anonymize(session: AsyncSession, contact: Contact, now: datetime) -> int:
    phone, name = contact.phone_e164, contact.profile_name
    messages = list(await session.scalars(
        select(Message).join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.contact_id == contact.id)
    ))  # fmt: skip
    for message in messages:
        message.body = _clean(message.body, phone, name)
        message.transcript = _clean(message.transcript, phone, name)
        message.media_ref = None
    if phone:
        await session.execute(
            update(Message)
            .where(Message.body.contains(phone))
            .values(body=func.replace(Message.body, phone, ANONYMIZED_MARK))
        )
    contact.profile_name = None
    contact.anonymized_at = now
    if contact.opted_out_at is None:
        contact.phone_e164 = None
    return len(messages)


def _clean(value: str | None, phone: str | None, name: str | None) -> str | None:
    scrubbed = scrub(value, phone, name)
    return mask_pii(scrubbed) if scrubbed is not None else None
