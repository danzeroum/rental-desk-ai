"""Painel do operador (RF23): saúde de cada imobiliária.

Roda com o papel da aplicação (sem BYPASSRLS): a lista de imobiliárias vem da
política `tenant_operator_read` (migração 0005, só para operador ativo) e as
métricas de cada uma são lidas com `tenant_session`, como qualquer requisição.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from app.core.rls import global_session, tenant_session
from app.domain.health import Health, Level, assess
from app.domain.onboarding import current, steps
from app.models import Tenant
from app.web.auth import PanelSession
from app.web.common import local_time, panel_session, render, require_operator
from app.web.routes_onboarding import onboarding_facts
from app.workers.health import collect, dead_jobs_by_tenant

router = APIRouter(prefix="/operador")
LEVEL_ORDER: Final = {Level.ERROR: 0, Level.ALERT: 1, Level.OK: 2}
STATUS_NAMES: Final = {
    "onboarding": "em implantação",
    "shadow": "modo sombra",
    "active": "atendendo",
    "suspended": "suspensa",
}


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
    onboarding: str | None = None
    """"passo N de 7: <título>" enquanto a imobiliária não está atendendo (RF28)."""


def _fmt(moment: datetime | None) -> str:
    return local_time(moment, "%d/%m %H:%M") if moment else "nunca"


async def tenant_rows(request: Request, operator: PanelSession, now: datetime) -> list[TenantRow]:
    async with global_session(request.app.state.sessions, user_id=operator.user_uuid) as session:
        tenants = list((await session.execute(select(Tenant).order_by(Tenant.name))).scalars())
    dead = await dead_jobs_by_tenant(request.app.state.redis)
    rows = []
    for tenant in tenants:
        snap = await collect(
            request.app.state.sessions, tenant, dead_jobs=dead.get(str(tenant.id), 0), now=now,
            user_id=operator.user_uuid,
        )  # fmt: skip
        data, account = snap.data, snap.account
        onboarding = None
        if tenant.status != "active":
            async with tenant_session(
                request.app.state.sessions, tenant.id, user_id=operator.user_uuid
            ) as session:
                facts = await onboarding_facts(session, tenant)
            step = current(steps(facts, now))
            onboarding = f"passo {step.number} de 7: {step.title}" if step else None
        rows.append(
            TenantRow(
                tenant=tenant, health=assess(data, now), last_inbound=_fmt(data.last_inbound_at),
                updated=_fmt(data.availability_updated_at), open_handoffs=data.open_handoffs,
                quality=(account.quality_rating or "—") if account else "sem número",
                test_number=bool(account and account.is_test_number),
                onboarding=onboarding,
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
        {"rows": rows, "summary": summary, "checked": _fmt(now), "status_names": STATUS_NAMES},
    )  # fmt: skip
