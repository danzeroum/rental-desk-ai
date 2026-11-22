"""Modo sombra no painel: conferir o que o assistente responderia e ativar (SPEC seção 11)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import append_audit
from app.domain.lifecycle import TRANSITIONS, Readiness, TransitionError, check_transition
from app.domain.notices import reason_label
from app.models import AuditLog, Message, Tenant, WhatsAppAccount
from app.models.enums import Direction
from app.web.common import local_time, redirect, render, verify_csrf
from app.web.context import ManagerCtx, TenantContext

router = APIRouter(prefix="/painel")
SHOWN: Final = 30
STATUS_LABELS: Final = {
    "onboarding": "Em implantação (o assistente não responde)",
    "shadow": "Modo sombra (o assistente só registra o que responderia)",
    "active": "Atendendo",
    "suspended": "Suspensa pelo operador",
}


async def _readiness(session: AsyncSession, tenant: Tenant) -> Readiness:
    shadow_since = await session.scalar(
        select(func.max(AuditLog.created_at)).where(
            AuditLog.event_type == "status_changed", AuditLog.payload["to"].astext == "shadow"
        )
    )
    query = (
        select(func.count()).select_from(AuditLog).where(AuditLog.event_type == "shadow_response")
    )
    if shadow_since is not None:
        query = query.where(AuditLog.created_at >= shadow_since)
    responses = await session.scalar(query)
    has_number = await session.scalar(select(func.count()).select_from(WhatsAppAccount))
    return Readiness(
        has_approved_data=tenant.active_snapshot_id is not None,
        has_number=bool(has_number),
        shadow_since=shadow_since,
        shadow_responses=int(responses or 0),
    )


async def _samples(session: AsyncSession) -> list[dict[str, Any]]:
    events = (
        await session.execute(
            select(AuditLog)
            .where(AuditLog.event_type == "shadow_response")
            .order_by(AuditLog.seq.desc())
            .limit(SHOWN)
        )
    ).scalars()
    samples = []
    for event in events:
        guest = await session.scalar(
            select(func.string_agg(Message.body, " / "))
            .where(
                Message.conversation_id == event.conversation_id,
                Message.direction == Direction.INBOUND,
                Message.processed_at <= event.created_at,
            )
            .group_by(Message.processed_at)
            .order_by(Message.processed_at.desc())
            .limit(1)
        )
        handoff = event.payload.get("handoff")
        samples.append(
            {
                "when": local_time(event.created_at, "%d/%m %H:%M"),
                "guest": guest or "(áudio ou mensagem sem texto)",
                "reply": event.payload.get("text", ""),
                "handoff": reason_label(handoff) if handoff else None,
                "rejected": bool((event.payload.get("validator") or {}).get("rejections")),
            }
        )
    return samples


async def _state(ctx: TenantContext) -> tuple[Tenant, Readiness, list[dict[str, Any]]]:
    async with ctx.db() as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return tenant, await _readiness(session, tenant), await _samples(session)


@router.get("/sombra", response_class=HTMLResponse)
async def shadow_page(request: Request, ctx: ManagerCtx, error: str | None = None) -> Response:
    tenant, ready, samples = await _state(ctx)
    actions = [
        (target, label) for (src, target), label in TRANSITIONS.items() if src == tenant.status
    ]
    return render(
        request, "panel/shadow.html",
        {
            "tenant": tenant, "status_label": STATUS_LABELS.get(tenant.status, tenant.status),
            "ready": ready, "samples": samples, "actions": actions, "error": error,
            "since": local_time(ready.shadow_since) if ready.shadow_since else None,
        },
        status_code=400 if error else 200,
    )  # fmt: skip


@router.post("/situacao")
async def change_status(request: Request, ctx: ManagerCtx) -> Response:
    form = await verify_csrf(request)
    target = str(form.get("target", ""))
    now: datetime = ctx.now()
    async with ctx.db() as session:
        tenant = await session.get(Tenant, ctx.tenant_id, with_for_update=True)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        ready = await _readiness(session, tenant)
        error: str | None = None
        try:
            check_transition(tenant.status, target, ready, now, early=form.get("early") == "sim")
        except TransitionError as exc:
            error = str(exc)
        else:
            previous = tenant.status
            await session.execute(
                update(Tenant).where(Tenant.id == ctx.tenant_id).values(status=target)
            )
            await append_audit(
                session, tenant_id=ctx.tenant_id, event_type="status_changed",
                payload={"from": previous, "to": target, "user_id": str(ctx.user_id),
                         "early": form.get("early") == "sim", "at": now.isoformat()},
            )  # fmt: skip
    if error:
        return await shadow_page(request, ctx, error=error)
    return redirect("/painel/sombra")
