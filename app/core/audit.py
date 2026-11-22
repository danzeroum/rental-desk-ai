"""Trilha de auditoria (SPEC, seção 7, item 9).

O encadeamento por hash é feito no banco (gatilho `audit_log_chain`), para valer
qualquer que seja o caminho de escrita. Aqui ficam a inclusão e a verificação.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def append_audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    conversation_id: uuid.UUID | None = None,
    snapshot_hash: str | None = None,
) -> AuditLog:
    """Inclui um evento; `seq`, `prev_hash`, `hash` e `created_at` vêm do banco."""
    entry = AuditLog(
        tenant_id=tenant_id,
        event_type=event_type,
        payload=payload,
        conversation_id=conversation_id,
        snapshot_hash=snapshot_hash,
    )
    session.add(entry)
    await session.flush()
    await session.refresh(entry)
    return entry


async def first_broken_seq(session: AsyncSession, tenant_id: uuid.UUID) -> int | None:
    """Primeiro `seq` adulterado ou fora de ordem; `None` se a corrente estiver íntegra."""
    result = await session.execute(select(func.audit_log_verify(tenant_id)))
    broken: int | None = result.scalar_one()
    return broken
