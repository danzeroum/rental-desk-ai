"""Coleta da saúde de cada imobiliária (RF23), usada pelo painel do operador e pelos alertas.

Lê com `tenant_session` (RLS normal). Quem lista as imobiliárias é o chamador: o painel
pela política de leitura do operador, o scheduler pelo papel de operação.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.rls import tenant_session
from app.domain.health import STUCK_AFTER, HealthInput
from app.models import AuditLog, Handoff, Message, Tenant, WhatsAppAccount
from app.models.enums import Direction, HandoffStatus, TenantStatus
from app.workers.queue import DEAD_LETTER

DEAD_SCAN: Final = 1000
DAY: Final = timedelta(hours=24)
HOUR: Final = timedelta(hours=1)


@dataclass(frozen=True)
class TenantSnapshot:
    data: HealthInput
    account: WhatsAppAccount | None


async def dead_jobs_by_tenant(redis: Redis) -> dict[str, int]:
    """Tarefas na fila de falhas, por imobiliária (chave `tenant:conversa`)."""
    counts: dict[str, int] = {}
    entries: Any = await redis.xrevrange(DEAD_LETTER, count=DEAD_SCAN)
    for _, fields in entries:
        tenant = str(fields.get("key", "")).split(":", 1)[0]
        counts[tenant] = counts.get(tenant, 0) + 1
    return counts


async def collect(
    sessions: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    *,
    dead_jobs: int,
    now: datetime,
    user_id: uuid.UUID | None = None,
) -> TenantSnapshot:
    async with tenant_session(sessions, tenant.id, user_id=user_id) as session:
        last_inbound = await session.scalar(
            select(func.max(Message.created_at)).where(Message.direction == Direction.INBOUND)
        )
        stuck = await session.scalar(
            select(func.count()).select_from(Message).where(
                Message.direction == Direction.INBOUND, Message.processed_at.is_(None),
                Message.created_at < now - STUCK_AFTER,
            )
        )  # fmt: skip
        open_count, oldest = (
            await session.execute(
                select(func.count(), func.min(Handoff.created_at)).where(
                    Handoff.status == HandoffStatus.OPEN
                )
            )
        ).one()
        responses = (
            select(func.count()).select_from(AuditLog).where(AuditLog.event_type == "response_sent")
        )
        rejections = await session.scalar(
            responses.where(
                AuditLog.created_at >= now - DAY,
                func.jsonb_array_length(
                    func.coalesce(AuditLog.payload["validator"]["rejections"], func.jsonb("[]"))
                ) > 0,
            )
        )  # fmt: skip
        llm_failures = await session.scalar(
            responses.where(
                AuditLog.created_at >= now - HOUR,
                AuditLog.payload["llm_unavailable"].astext == "true",
            )
        )
        account = await session.scalar(select(WhatsAppAccount).limit(1))
    data = HealthInput(
        active=tenant.status == TenantStatus.ACTIVE,
        paused=tenant.paused_at is not None,
        last_inbound_at=last_inbound,
        availability_updated_at=tenant.availability_updated_at,
        open_handoffs=int(open_count or 0),
        oldest_open_handoff_at=oldest,
        validator_rejections_24h=int(rejections or 0),
        stuck_messages=int(stuck or 0),
        dead_jobs=dead_jobs,
        quality=account.quality_rating if account else None,
        has_approved_data=tenant.active_snapshot_id is not None,
        llm_failures_1h=int(llm_failures or 0),
    )
    return TenantSnapshot(data, account)
