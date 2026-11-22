"""Calendários iCal (RF26) e planilha Google (RF27) no painel. Só gestor (ou suporte)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import delete, select

from app.adapters.google_sheet import SheetError, sheet_id_from
from app.core.audit import append_audit
from app.models import CalendarFeed, Property, SheetLink, Tenant
from app.web.common import local_time, redirect, render, verify_csrf
from app.web.context import ManagerCtx, TenantContext
from app.workers.calendars import ALERT_AFTER_FAILURES, CalendarService, FeedError
from app.workers.sheets import SheetSyncService

router = APIRouter(prefix="/painel")


def calendars(request: Request) -> CalendarService:
    service: CalendarService = request.app.state.calendars
    return service


def sheets(request: Request) -> SheetSyncService:
    service: SheetSyncService = request.app.state.sheets
    return service


def sync_status(row: Any) -> dict[str, Any]:
    failing = row.consecutive_failures >= ALERT_AFTER_FAILURES
    return {
        "ok": row.last_success_at is not None and row.consecutive_failures == 0,
        "failing": failing,
        "failures": row.consecutive_failures,
        "error": row.last_error,
        "last_success": local_time(row.last_success_at, "%d/%m %H:%M")
        if row.last_success_at
        else None,
    }


# --- Calendários --------------------------------------------------------------------------


async def _calendar_context(ctx: TenantContext) -> dict[str, Any]:
    async with ctx.db() as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        codes = list(
            (
                await session.execute(
                    select(Property.code)
                    .where(Property.snapshot_id == (tenant.active_snapshot_id if tenant else None))
                    .order_by(Property.code)
                )
            ).scalars()
        )
        feeds = list(
            (
                await session.execute(select(CalendarFeed).order_by(CalendarFeed.property_code))
            ).scalars()
        )
    rows = [
        {"id": f.id, "code": f.property_code, "label": f.label, "hint": f.url_hint,
         "events": f.events, **sync_status(f)}
        for f in feeds
    ]  # fmt: skip
    covered = {f.property_code for f in feeds}
    return {"rows": rows, "codes": codes, "missing": [c for c in codes if c not in covered]}


@router.get("/calendarios", response_class=HTMLResponse)
async def calendar_page(request: Request, ctx: ManagerCtx, error: str | None = None) -> Response:
    values = await _calendar_context(ctx)
    return render(
        request,
        "panel/calendars.html",
        {**values, "error": error},
        status_code=400 if error else 200,
    )


@router.post("/calendarios")
async def add_calendar(request: Request, ctx: ManagerCtx) -> Response:
    form = await verify_csrf(request)
    code = str(form.get("codigo", "")).strip().upper()
    try:
        async with ctx.db() as session:
            feed = await calendars(request).add(
                session, ctx.tenant_id, ctx.user_id, code, str(form.get("url", ""))
            )
            feed_id = feed.id
    except FeedError as exc:
        return await calendar_page(request, ctx, error=f"Não salvei: {exc}.")
    await calendars(request).sync_feed(ctx.tenant_id, feed_id)
    return redirect("/painel/calendarios")


@router.post("/calendarios/{feed_id}/remover")
async def remove_calendar(request: Request, feed_id: uuid.UUID, ctx: ManagerCtx) -> Response:
    await verify_csrf(request)
    async with ctx.db() as session:
        feed = await session.get(CalendarFeed, feed_id)
        if feed is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        await session.execute(
            delete(CalendarFeed).where(CalendarFeed.id == feed_id)
        )  # bloqueios saem junto
        await append_audit(
            session, tenant_id=ctx.tenant_id, event_type="calendar_feed_removed",
            payload={"feed_id": str(feed_id), "codigo": feed.property_code,
                     "user_id": str(ctx.user_id)},
        )  # fmt: skip
    return redirect("/painel/calendarios")


@router.post("/calendarios/sincronizar")
async def sync_calendars_now(request: Request, ctx: ManagerCtx) -> Response:
    await verify_csrf(request)
    await calendars(request).sync_tenant(ctx.tenant_id)
    return redirect("/painel/calendarios")


# --- Planilha Google ------------------------------------------------------------------------


async def sheet_context(request: Request, ctx: TenantContext) -> dict[str, Any]:
    async with ctx.db() as session:
        link = await session.scalar(select(SheetLink))
    return {
        "sheet": {"id": link.sheet_id, **sync_status(link)} if link else None,
        "service_email": sheets(request).source.service_email,
    }


@router.post("/planilha-google")
async def link_sheet(request: Request, ctx: ManagerCtx) -> Response:
    form = await verify_csrf(request)
    try:
        sheet_id = sheet_id_from(str(form.get("link", "")))
    except SheetError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Não salvei: {exc}.") from exc
    async with ctx.db() as session:
        link = await session.scalar(select(SheetLink).with_for_update())
        if link is None:
            session.add(
                SheetLink(tenant_id=ctx.tenant_id, sheet_id=sheet_id, created_by=ctx.user_id)
            )
        else:
            link.sheet_id, link.last_content_hash = sheet_id, None
            link.consecutive_failures, link.last_error, link.alerted_at = 0, None, None
        await append_audit(
            session, tenant_id=ctx.tenant_id, event_type="sheet_linked",
            payload={"user_id": str(ctx.user_id)},
        )  # fmt: skip
    await sheets(request).sync(ctx.tenant_id)
    return redirect("/painel/dados")


@router.post("/planilha-google/{action}")
async def sheet_action(request: Request, action: str, ctx: ManagerCtx) -> Response:
    await verify_csrf(request)
    if action == "sincronizar":
        await sheets(request).sync(ctx.tenant_id)
    elif action == "desligar":
        async with ctx.db() as session:
            await session.execute(delete(SheetLink))
            await append_audit(
                session, tenant_id=ctx.tenant_id, event_type="sheet_unlinked",
                payload={"user_id": str(ctx.user_id)},
            )  # fmt: skip
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return redirect("/painel/dados")
