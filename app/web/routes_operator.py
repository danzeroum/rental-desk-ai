"""Painel do operador (RF23): saúde de cada imobiliária.

Roda com o papel da aplicação (sem BYPASSRLS): a lista de imobiliárias vem da
política `tenant_operator_read` (migração 0005, só para operador ativo) e as
métricas de cada uma são lidas com `tenant_session`, como qualquer requisição.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.core.rls import global_session, tenant_session
from app.domain.health import STUCK_AFTER, Health, HealthInput, Level, assess
from app.models import AuditLog, Handoff, Message, Tenant, WhatsAppAccount
from app.models.enums import Direction, HandoffStatus, TenantStatus
from app.web.auth import PanelSession
from app.web.common import local_time, panel_session, render, require_operator
from app.workers.queue import DEAD_LETTER

router = APIRouter(prefix="/operador")
DEAD_SCAN: Final = 1000
DAY: Final = timedelta(hours=24)
LEVEL_ORDER: Final = {Level.ERROR: 0, Level.ALERT: 1, Level.OK: 2}


async def operator_session(panel: Annotated[PanelSession, Depends(panel_session)]) -> PanelSession:
    return require_operator(panel)


Operator = Annotated[PanelSession, Depends(operator_session)]


@dataclass
class TenantRow:
    tenant: Tenant
    health: Health
    last_inbound: str
    updated: str
    open_handoffs: int
    quality: str
    test_number: bool


def _fmt(moment: datetime | None) -> str:
    return local_time(moment, "%d/%m %H:%M") if moment else "nunca"


async def dead_jobs_by_tenant(redis: Redis) -> dict[str, int]:
    """Tarefas na fila de falhas, por imobiliária (chave `tenant:conversa`)."""
    counts: dict[str, int] = {}
    entries: Any = await redis.xrevrange(DEAD_LETTER, count=DEAD_SCAN)
    for _, fields in entries:
        tenant = str(fields.get("key", "")).split(":", 1)[0]
        counts[tenant] = counts.get(tenant, 0) + 1
    return counts


async def _health_input(
    request: Request, tenant: Tenant, user_id: uuid.UUID, dead: int, now: datetime
) -> tuple[HealthInput, WhatsAppAccount | None]:
    since = now - DAY
    async with tenant_session(request.app.state.sessions, tenant.id, user_id=user_id) as session:
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
        rejections = await session.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.event_type == "response_sent", AuditLog.created_at >= since,
                func.jsonb_array_length(
                    func.coalesce(AuditLog.payload["validator"]["rejections"], func.jsonb("[]"))
                ) > 0,
            )
        )  # fmt: skip
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
        dead_jobs=dead,
        quality=account.quality_rating if account else None,
        has_approved_data=tenant.active_snapshot_id is not None,
    )
    return data, account


async def tenant_rows(request: Request, operator: PanelSession, now: datetime) -> list[TenantRow]:
    async with global_session(request.app.state.sessions, user_id=operator.user_uuid) as session:
        tenants = list((await session.execute(select(Tenant).order_by(Tenant.name))).scalars())
    dead = await dead_jobs_by_tenant(request.app.state.redis)
    rows = []
    for tenant in tenants:
        data, account = await _health_input(
            request, tenant, operator.user_uuid, dead.get(str(tenant.id), 0), now
        )
        rows.append(
            TenantRow(
                tenant=tenant, health=assess(data, now), last_inbound=_fmt(data.last_inbound_at),
                updated=_fmt(data.availability_updated_at), open_handoffs=data.open_handoffs,
                quality=(account.quality_rating or "—") if account else "sem número",
                test_number=bool(account and account.is_test_number),
            )
        )  # fmt: skip
    return sorted(rows, key=lambda r: (LEVEL_ORDER[r.health.level], r.tenant.name))


@router.get("", response_class=HTMLResponse)
async def overview(request: Request, operator: Operator) -> Response:
    now = request.app.state.auth.clock()
    rows = await tenant_rows(request, operator, now)
    summary = {level: sum(r.health.level is level for r in rows) for level in Level}
    return render(
        request, "operator/overview.html",
        {"rows": rows, "summary": summary, "checked": _fmt(now)},
    )  # fmt: skip
