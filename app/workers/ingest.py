"""Ingestão do webhook: grava as mensagens e agenda o processamento (SPEC, seção 3, passos 1-2).

Idempotente pelo ID externo (RF01): a mesma mensagem reenviada pela Meta é
gravada e agendada uma vez só. O commit no banco acontece antes do agendamento,
para o worker sempre encontrar a mensagem.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.whatsapp_webhook import EchoMessage, InboundMessage, WebhookBatch
from app.core.logging import bind_context, get_logger
from app.core.rls import global_session, tenant_session
from app.models import Contact, Conversation, Handoff, Message, TenantRoute
from app.models.enums import ConversationStatus, Direction, HandoffStatus, MessageOrigin
from app.workers.queue import RateDecision, WorkQueue, conversation_key

log = get_logger(__name__)

NOTICE_JOB = "rate_limit_notice"
ECHO_MUTE_FOR = timedelta(hours=24)


@dataclass
class IngestResult:
    stored: int = 0
    duplicates: int = 0
    unknown_number: int = 0
    scheduled: int = 0
    silenced: int = 0
    echoes: int = 0


def e164(wa_id: str) -> str:
    return f"+{wa_id}"


async def _route(
    sessions: async_sessionmaker[AsyncSession], phone_number_id: str
) -> uuid.UUID | None:
    async with global_session(sessions) as session:
        tenant_id: uuid.UUID | None = await session.scalar(
            select(TenantRoute.tenant_id).where(
                TenantRoute.channel == "whatsapp", TenantRoute.external_id == phone_number_id
            )
        )
    return tenant_id


async def _upsert_contact(
    session: AsyncSession, tenant_id: uuid.UUID, wa_id: str, profile_name: str | None
) -> tuple[uuid.UUID, bool]:
    """Devolve (id do contato, é novo?)."""
    statement = (
        insert(Contact)
        .values(tenant_id=tenant_id, phone_e164=e164(wa_id), profile_name=profile_name)
        .on_conflict_do_nothing(index_elements=["tenant_id", "phone_e164"])
        .returning(Contact.id)
    )
    created = await session.scalar(statement)
    if created is not None:
        return created, True
    existing = await session.scalar(
        select(Contact.id).where(Contact.tenant_id == tenant_id, Contact.phone_e164 == e164(wa_id))
    )
    if existing is None:  # pragma: no cover - corrida já resolvida pelo ON CONFLICT
        raise RuntimeError("contato não encontrado após inserção")
    return existing, False


async def _open_conversation(
    session: AsyncSession, tenant_id: uuid.UUID, contact_id: uuid.UUID
) -> uuid.UUID:
    current = await session.scalar(
        select(Conversation.id)
        .where(
            Conversation.contact_id == contact_id, Conversation.status != ConversationStatus.CLOSED
        )
        .order_by(Conversation.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if current is not None:
        return current
    conversation = Conversation(tenant_id=tenant_id, contact_id=contact_id)
    session.add(conversation)
    await session.flush()
    return conversation.id


async def _insert_message(session: AsyncSession, values: dict[str, object]) -> bool:
    statement = (
        insert(Message)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "external_id"],
            index_where=Message.external_id.is_not(None),
        )
        .returning(Message.id)
    )
    return await session.scalar(statement) is not None


async def _store_inbound(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    msg: InboundMessage,
    now: datetime,
) -> tuple[uuid.UUID, uuid.UUID, bool] | None:
    """Grava a mensagem; `None` se já existia. Devolve (contato, conversa, contato novo?)."""
    async with tenant_session(sessions, tenant_id) as session:
        contact_id, is_new = await _upsert_contact(session, tenant_id, msg.wa_id, msg.profile_name)
        conversation_id = await _open_conversation(session, tenant_id, contact_id)
        inserted = await _insert_message(
            session,
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "direction": Direction.INBOUND,
                "kind": msg.kind,
                "external_id": msg.external_id,
                "body": msg.text,
                "media_ref": msg.media_id,
                "external_timestamp": msg.timestamp,
                "origin": MessageOrigin.GUEST,
            },
        )
        if not inserted:
            return None
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(
                last_message_at=now,
                first_inbound_at=func.coalesce(Conversation.first_inbound_at, now),
            )
        )
    return contact_id, conversation_id, is_new


async def _store_echo(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    echo: EchoMessage,
    now: datetime,
    *,
    mute: bool = False,
) -> bool:
    async with tenant_session(sessions, tenant_id) as session:
        contact_id, _ = await _upsert_contact(session, tenant_id, echo.to_wa_id, None)
        conversation_id = await _open_conversation(session, tenant_id, contact_id)
        inserted = await _insert_message(
            session,
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "direction": Direction.OUTBOUND,
                "kind": echo.kind,
                "external_id": echo.external_id,
                "body": echo.text,
                "external_timestamp": echo.timestamp,
                "origin": MessageOrigin.BUSINESS_APP,
            },
        )
        if inserted:
            values: dict[str, object] = {"last_message_at": now}
            if mute:
                # RF11 por eco: só com WHATSAPP_ECHO_MUTE=true (coexistência comprovada).
                values.update(muted_until=now + ECHO_MUTE_FOR, status=ConversationStatus.HUMAN)
                await session.execute(
                    update(Handoff)
                    .where(
                        Handoff.conversation_id == conversation_id,
                        Handoff.status == HandoffStatus.OPEN,
                    )
                    .values(status=HandoffStatus.ASSUMED, assumed_at=now, assumed_via="echo")
                )
            await session.execute(
                update(Conversation).where(Conversation.id == conversation_id).values(**values)
            )
    return inserted


async def ingest_batch(
    batch: WebhookBatch,
    *,
    sessions: async_sessionmaker[AsyncSession],
    queue: WorkQueue,
    now: datetime,
    echo_mute: bool = False,
) -> IngestResult:
    result = IngestResult()
    routes: dict[str, uuid.UUID | None] = {}

    async def route(phone_number_id: str) -> uuid.UUID | None:
        if phone_number_id not in routes:
            routes[phone_number_id] = await _route(sessions, phone_number_id)
        return routes[phone_number_id]

    for msg in batch.messages:
        tenant_id = await route(msg.phone_number_id)
        if tenant_id is None:
            result.unknown_number += 1
            log.warning("webhook_numero_desconhecido", phone_number_id=msg.phone_number_id)
            continue
        bind_context(tenant_id=str(tenant_id))
        stored = await _store_inbound(sessions, tenant_id, msg, now)
        if stored is None:
            result.duplicates += 1
            continue
        contact_id, conversation_id, is_new = stored
        result.stored += 1
        key = conversation_key(tenant_id, conversation_id)
        decision = await queue.register_inbound(tenant_id, contact_id, msg.external_id)
        if decision is RateDecision.OK:
            await queue.schedule(key, priority=is_new)
            result.scheduled += 1
        elif decision is RateDecision.JUST_SILENCED:
            await queue.enqueue(NOTICE_JOB, key, priority=True)
            result.silenced += 1
            log.warning("limite_anti_abuso_atingido", conversation_id=str(conversation_id))
        else:
            result.silenced += 1

    for echo in batch.echoes:
        tenant_id = await route(echo.phone_number_id)
        if tenant_id is not None and await _store_echo(
            sessions, tenant_id, echo, now, mute=echo_mute
        ):
            result.echoes += 1
    log.info(
        "webhook_processado",
        stored=result.stored,
        duplicates=result.duplicates,
        unknown_number=result.unknown_number,
        scheduled=result.scheduled,
        silenced=result.silenced,
        echoes=result.echoes,
        discarded=batch.discarded,
    )
    return result
