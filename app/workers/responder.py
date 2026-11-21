"""Envio de uma resposta ao hóspede: gateway → mensagem de saída → audit_log (regra 8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.whatsapp import WhatsAppGateway
from app.core.audit import append_audit
from app.core.crypto import Keyring
from app.core.logging import get_logger
from app.core.rls import tenant_session
from app.models import Contact, Conversation, Message
from app.models.enums import Direction, MessageKind, MessageOrigin
from app.workers.channel import channel_for

log = get_logger(__name__)


class RecipientUnavailableError(Exception):
    """Contato sem telefone (anonimizado) ou com opt-out."""


async def send_reply(
    sessions: async_sessionmaker[AsyncSession],
    gateway: WhatsAppGateway,
    keyring: Keyring,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    text: str,
    event_type: str,
    audit_payload: dict[str, Any],
    snapshot_hash: str | None,
    now: datetime,
) -> str:
    """Envia `text`, grava a mensagem de saída e o evento de auditoria. Devolve o wamid."""
    async with tenant_session(sessions, tenant_id) as session:
        row = (
            await session.execute(
                select(Contact.phone_e164, Contact.opted_out_at)
                .join(Conversation, Conversation.contact_id == Contact.id)
                .where(Conversation.id == conversation_id)
            )
        ).one()
        if row.phone_e164 is None or row.opted_out_at is not None:
            raise RecipientUnavailableError("contato indisponível para mensagens")
        channel = await channel_for(session, tenant_id, keyring)
    external_id = await gateway.send_text(channel, row.phone_e164.removeprefix("+"), text)
    async with tenant_session(sessions, tenant_id) as session:
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                direction=Direction.OUTBOUND,
                kind=MessageKind.TEXT,
                external_id=external_id,
                body=text,
                origin=MessageOrigin.ASSISTANT,
            )
        )
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(
                last_message_at=now,
                first_response_at=func.coalesce(Conversation.first_response_at, now),
            )
        )
        await append_audit(
            session,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            event_type=event_type,
            snapshot_hash=snapshot_hash,
            payload={**audit_payload, "text": text, "external_id": external_id},
        )
    log.info("resposta_enviada", event_type=event_type, conversation_id=str(conversation_id))
    return external_id
