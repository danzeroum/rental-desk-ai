"""Direitos do titular por telefone (RF21): consultar, exportar e apagar.

Apagar remove o contato e, em cascata, conversas, mensagens (com textos,
transcrições e referências de anexo), extrações, buscas e passagens; o telefone
também sai do texto das confirmações enviadas a corretores. O `audit_log` fica
(imutável, prazo legal) porque não guarda telefone, nome nem texto do hóspede —
só ids internos que deixam de apontar para alguém. Logs técnicos não têm dado
pessoal (mascarados) e somem em 30 dias.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import append_audit
from app.core.logging import get_logger
from app.models import AuditLog, Contact, Conversation, Handoff, Intent, Message
from app.models.enums import HandoffStatus

log = get_logger(__name__)
ERASED_MARK = "[apagado a pedido]"


@dataclass(frozen=True)
class SubjectSummary:
    contact_id: uuid.UUID
    conversations: int
    messages: int
    open_handoffs: int
    first_at: datetime | None
    last_at: datetime | None
    opted_out: bool


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def find(session: AsyncSession, phone_e164: str) -> SubjectSummary | None:
    contact = await session.scalar(select(Contact).where(Contact.phone_e164 == phone_e164))
    if contact is None:
        return None
    conversations = select(Conversation.id).where(Conversation.contact_id == contact.id)
    messages, first_at, last_at = (
        await session.execute(
            select(func.count(), func.min(Message.created_at), func.max(Message.created_at)).where(
                Message.conversation_id.in_(conversations)
            )
        )
    ).one()
    return SubjectSummary(
        contact_id=contact.id,
        conversations=int(
            await session.scalar(select(func.count()).select_from(conversations.subquery())) or 0
        ),
        messages=int(messages or 0),
        open_handoffs=int(
            await session.scalar(
                select(func.count())
                .select_from(Handoff)
                .where(
                    Handoff.conversation_id.in_(conversations),
                    Handoff.status != HandoffStatus.CLOSED,
                )
            )
            or 0
        ),
        first_at=first_at,
        last_at=last_at,
        opted_out=contact.opted_out_at is not None,
    )


async def export(session: AsyncSession, phone_e164: str) -> dict[str, Any] | None:
    """Tudo o que existe sobre o número, em JSON legível (portabilidade, LGPD art. 18)."""
    contact = await session.scalar(select(Contact).where(Contact.phone_e164 == phone_e164))
    if contact is None:
        return None
    conversations = list(
        (
            await session.execute(
                select(Conversation)
                .where(Conversation.contact_id == contact.id)
                .order_by(Conversation.created_at)
            )
        ).scalars()
    )
    result: dict[str, Any] = {
        "telefone": contact.phone_e164,
        "nome_do_perfil": contact.profile_name,
        "primeiro_contato": _iso(contact.created_at),
        "sem_mensagens_automaticas_desde": _iso(contact.opted_out_at),
        "conversas": [],
    }
    for conversation in conversations:
        messages = (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at)
            )
        ).scalars()
        handoffs = (
            await session.execute(select(Handoff).where(Handoff.conversation_id == conversation.id))
        ).scalars()
        intents = (
            await session.execute(select(Intent).where(Intent.conversation_id == conversation.id))
        ).scalars()
        events = (
            await session.execute(
                select(AuditLog.event_type, AuditLog.created_at)
                .where(AuditLog.conversation_id == conversation.id)
                .order_by(AuditLog.seq)
            )
        ).all()
        result["conversas"].append(
            {
                "inicio": _iso(conversation.created_at),
                "pedido_entendido": conversation.state_json.get("slots"),
                "mensagens": [
                    {
                        "quando": _iso(m.created_at),
                        "de": "você" if m.direction == "in" else "imobiliária",
                        "tipo": m.kind,
                        "texto": m.body,
                        "transcricao_do_audio": m.transcript,
                    }
                    for m in messages
                ],
                "passagens_ao_corretor": [
                    {
                        "quando": _iso(h.created_at),
                        "motivo": h.reason,
                        "resumo": h.summary,
                        "situacao": h.status,
                    }
                    for h in handoffs
                ],
                "extracoes_do_pedido": [i.payload for i in intents],
                "registro_de_auditoria": [
                    {"evento": e.event_type, "quando": _iso(e.created_at)} for e in events
                ],
            }
        )
    return result


async def erase(
    session: AsyncSession, *, tenant_id: uuid.UUID, phone_e164: str, user_id: uuid.UUID
) -> SubjectSummary | None:
    summary = await find(session, phone_e164)
    if summary is None:
        return None
    await session.execute(delete(Contact).where(Contact.id == summary.contact_id))
    scrubbed = await session.execute(
        update(Message)
        .where(Message.body.contains(phone_e164))
        .values(body=func.replace(Message.body, phone_e164, ERASED_MARK))
    )
    await append_audit(
        session, tenant_id=tenant_id, event_type="subject_erased",
        payload={
            "contact_id": str(summary.contact_id), "by": str(user_id),
            "conversations": summary.conversations, "messages": summary.messages,
            "mentions_scrubbed": scrubbed.rowcount,  # type: ignore[attr-defined]
        },
    )  # fmt: skip
    log.info("titular_apagado", conversations=summary.conversations)
    return summary
