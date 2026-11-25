"""Painel do gestor: escolher imobiliária, início, pausa (RF19) e passagens (RF11, RF17)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select

from app.domain.access import parse_reason
from app.domain.notices import reason_label, summary_text
from app.domain.search import FRESHNESS_LIMIT
from app.models import AppUser, Contact, Conversation, DataSnapshot, Handoff, Tenant
from app.models.enums import HandoffOutcome, HandoffStatus, SnapshotStatus
from app.web.auth import AuthError, MissingReasonError
from app.web.common import (
    SESSION_COOKIE,
    auth,
    local_time,
    redirect,
    render,
    safe_next,
    verify_csrf,
)
from app.web.context import ActivePanel, ManagerCtx, TenantContext, TenantCtx
from app.workers.handoffs import HandoffService, HandoffStateError

router = APIRouter(prefix="/painel")
OUTCOME_LABELS: Final = {
    HandoffOutcome.WON.value: "Fechou",
    HandoffOutcome.LOST.value: "Perdeu",
    HandoffOutcome.PENDING.value: "Pendente",
}
STATUS_LABELS: Final = {"open": "Aguardando", "assumed": "Assumida", "closed": "Encerrada"}
HANDOFF_PAGE: Final = 50


def handoffs(request: Request) -> HandoffService:
    service: HandoffService = request.app.state.handoffs
    return service


def local(moment: datetime | None) -> str:
    return local_time(moment, "%d/%m %H:%M")


# --- Escolher imobiliária --------------------------------------------------------------


@router.get("/escolher", response_class=HTMLResponse)
async def choose_page(request: Request, panel: ActivePanel) -> Response:
    service = auth(request)
    memberships = await service.memberships(panel.user_uuid)
    names = await service.tenant_names([m.tenant_id for m in memberships], panel.user_uuid)
    options = sorted(
        (
            {"id": m.tenant_id, "name": names.get(m.tenant_id, "?"), "role": m.role}
            for m in memberships
        ),
        key=lambda o: str(o["name"]),
    )
    if not options and panel.is_operator:
        return redirect("/operador")
    return render(request, "panel/choose.html", {"options": options})


@router.post("/escolher")
async def choose(request: Request, panel: ActivePanel) -> Response:
    form = await verify_csrf(request)
    reason = parse_reason(str(form.get("motivo", "")), str(form.get("detalhe", "")))
    try:
        tenant_id = uuid.UUID(str(form.get("tenant_id", "")))
        await auth(request).choose_tenant(request.cookies[SESSION_COOKIE], panel, tenant_id, reason)
    except MissingReasonError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except (ValueError, AuthError) as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Você não tem acesso a essa imobiliária."
        ) from exc
    return redirect("/painel")


# --- Início -------------------------------------------------------------------------------


@dataclass
class Overview:
    tenant: Tenant
    active_version: int | None
    drafts: int
    open_handoffs: int
    stale: bool


async def _overview(ctx: TenantContext) -> Overview:
    async with ctx.db() as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        active = (
            await session.get(DataSnapshot, tenant.active_snapshot_id)
            if tenant.active_snapshot_id
            else None
        )
        drafts = await session.scalar(
            select(func.count())
            .select_from(DataSnapshot)
            .where(DataSnapshot.status == SnapshotStatus.DRAFT)
        )
        open_count = await session.scalar(
            select(func.count()).select_from(Handoff).where(Handoff.status == HandoffStatus.OPEN)
        )
    updated = tenant.availability_updated_at
    stale = updated is None or ctx.now() - updated > FRESHNESS_LIMIT
    return Overview(tenant, active.version if active else None, drafts or 0, open_count or 0, stale)


@router.get("", response_class=HTMLResponse)
async def home(request: Request, ctx: TenantCtx) -> Response:
    overview = await _overview(ctx)
    return render(
        request, "panel/home.html",
        {"o": overview, "updated": local(overview.tenant.availability_updated_at), "ctx": ctx},
    )  # fmt: skip


@router.post("/pausa")
async def pause(request: Request, ctx: ManagerCtx) -> Response:
    form = await verify_csrf(request)
    paused = form.get("action") == "pausar"
    await handoffs(request).set_paused(ctx.tenant_id, paused=paused, user_id=ctx.user_id)
    return redirect("/painel")


# --- Passagens ----------------------------------------------------------------------------


async def _handoff_rows(ctx: TenantContext, show: str) -> list[dict[str, Any]]:
    query = (
        select(Handoff, Contact.phone_e164, AppUser.display_name, Contact.opted_out_at)
        .join(Conversation, Conversation.id == Handoff.conversation_id)
        .join(Contact, Contact.id == Conversation.contact_id)
        .outerjoin(AppUser, AppUser.id == Handoff.assumed_by)
        .order_by(Handoff.created_at.desc())
        .limit(HANDOFF_PAGE)
    )
    if show == "abertas":
        query = query.where(Handoff.status != HandoffStatus.CLOSED)
    elif show == "sem-desfecho":
        query = query.where(Handoff.status == HandoffStatus.ASSUMED, Handoff.outcome.is_(None))
    async with ctx.db() as session:
        rows = (await session.execute(query)).all()
    return [
        {
            "id": h.id,
            "created": local(h.created_at),
            "reason": reason_label(h.reason),
            "summary": summary_text(h.summary),
            "status": h.status,
            "status_label": STATUS_LABELS.get(h.status, h.status),
            "guest": phone or "(apagado a pedido)",
            "assumed_by": name,
            "assumed": local(h.assumed_at) if h.assumed_at else None,
            "outcome": OUTCOME_LABELS.get(h.outcome or "", None),
            "opted_out": opted_out is not None,
        }
        for h, phone, name, opted_out in rows
    ]


@router.get("/passagens", response_class=HTMLResponse)
async def handoff_list(request: Request, ctx: TenantCtx, show: str = "abertas") -> Response:
    show = show if show in {"abertas", "sem-desfecho", "todas"} else "abertas"
    rows = await _handoff_rows(ctx, show)
    return render(
        request, "panel/handoffs.html",
        {"rows": rows, "show": show, "outcomes": OUTCOME_LABELS, "ctx": ctx},
    )  # fmt: skip


@router.post("/passagens/{handoff_id}/assumir")
async def assume(request: Request, handoff_id: uuid.UUID, ctx: TenantCtx) -> Response:
    await verify_csrf(request)
    try:
        await handoffs(request).assume(ctx.tenant_id, handoff_id, ctx.user_id, via="panel")
    except HandoffStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return redirect("/painel/passagens")


@router.post("/passagens/{handoff_id}/desfecho")
async def outcome(request: Request, handoff_id: uuid.UUID, ctx: ManagerCtx) -> Response:
    form = await verify_csrf(request)
    try:
        value = HandoffOutcome(str(form.get("outcome", "")))
        await handoffs(request).record_outcome(ctx.tenant_id, handoff_id, value, ctx.user_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Desfecho inválido.") from exc
    except HandoffStateError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return redirect(safe_next(form.get("next"), "/painel/passagens?show=sem-desfecho"))
