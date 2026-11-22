"""Planilha (RF14, RF15) e datas das casas (RF13, RF16) no painel."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from typing import Any, Final

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import delete, select
from starlette.datastructures import UploadFile

from app.core.audit import append_audit
from app.core.config import TIMEZONE
from app.core.logging import get_logger
from app.domain.render import br_date, brl
from app.importer.model import ImportReport
from app.importer.service import (
    SnapshotStateError,
    approve_snapshot,
    prepare_import,
    reject_snapshot,
    save_draft,
)
from app.importer.workbook import build_workbook
from app.models import Block, DataSnapshot, Property, Rate, Tenant
from app.models.enums import BlockSource, SnapshotStatus
from app.web.common import local_time, redirect, render, verify_csrf
from app.web.context import ManagerCtx, TenantContext, TenantCtx

router = APIRouter(prefix="/painel")
log = get_logger(__name__)
MAX_UPLOAD_BYTES: Final = 5 * 1024 * 1024
XLSX_TYPE: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
REMOVABLE_SOURCES: Final = frozenset({BlockSource.PANEL.value, BlockSource.REMINDER.value})
SOURCE_LABELS: Final = {
    "spreadsheet": "planilha", "panel": "painel", "reminder": "lembrete das 9h",
    "ical": "calendário", "google_sheet": "planilha Google",
}  # fmt: skip


def _today(ctx: TenantContext) -> date:
    return ctx.now().astimezone(TIMEZONE).date()


# --- Planilha -------------------------------------------------------------------------


@router.get("/dados", response_class=HTMLResponse)
async def data_page(request: Request, ctx: ManagerCtx) -> Response:
    async with ctx.db() as session:
        snapshots = list(
            (
                await session.execute(
                    select(DataSnapshot).order_by(DataSnapshot.version.desc()).limit(20)
                )
            ).scalars()
        )
    return render(request, "panel/data.html", {"snapshots": snapshots, "ctx": ctx})


@router.get("/dados/modelo")
async def download_template(ctx: TenantCtx) -> Response:
    return Response(
        build_workbook(), media_type=XLSX_TYPE,
        headers={"Content-Disposition": 'attachment; filename="planilha-modelo.xlsx"'},
    )  # fmt: skip


async def _read_upload(upload: UploadFile) -> bytes:
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Arquivo maior que 5 MB.")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Escolha o arquivo da planilha.")
    return content


@router.post("/dados")
async def upload(request: Request, ctx: ManagerCtx) -> Response:
    form = await verify_csrf(request)
    file = form.get("arquivo")
    if not isinstance(file, UploadFile):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Escolha o arquivo da planilha.")
    content = await _read_upload(file)
    report: ImportReport = await prepare_import(
        content, today=_today(ctx), link_checker=request.app.state.link_checker
    )
    if not report.ok:
        return render(
            request, "panel/data_errors.html",
            {"report": report, "file_name": file.filename, "ctx": ctx}, status_code=400,
        )  # fmt: skip
    async with ctx.db() as session:
        snapshot = await save_draft(
            session, tenant_id=ctx.tenant_id, user_id=ctx.user_id, report=report,
            file_name=(file.filename or "planilha.xlsx")[:255],
        )  # fmt: skip
    return redirect(f"/painel/dados/{snapshot.id}")


def _rate_ranges(rates: list[Rate]) -> dict[str, str]:
    by_code: dict[str, list[int]] = defaultdict(list)
    for rate in rates:
        by_code[rate.property_code].append(rate.daily_cents)
    return {
        code: brl(min(v)) if min(v) == max(v) else f"{brl(min(v))} a {brl(max(v))}"
        for code, v in by_code.items()
    }


async def _codes(session: Any, snapshot_id: uuid.UUID | None) -> set[str]:
    if snapshot_id is None:
        return set()
    return set(
        (
            await session.execute(select(Property.code).where(Property.snapshot_id == snapshot_id))
        ).scalars()
    )


@router.get("/dados/{snapshot_id}", response_class=HTMLResponse)
async def preview(request: Request, snapshot_id: uuid.UUID, ctx: ManagerCtx) -> Response:
    async with ctx.db() as session:
        snapshot = await session.get(DataSnapshot, snapshot_id)
        if snapshot is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        tenant = await session.get(Tenant, ctx.tenant_id)
        properties = list(
            (await session.execute(
                select(Property).where(Property.snapshot_id == snapshot_id).order_by(Property.code)
            )).scalars()
        )  # fmt: skip
        rates = list(
            (await session.execute(select(Rate).where(Rate.snapshot_id == snapshot_id))).scalars()
        )
        current = await _codes(session, tenant.active_snapshot_id if tenant else None)
    codes = {p.code for p in properties}
    return render(
        request, "panel/data_preview.html",
        {
            "snapshot": snapshot, "properties": properties, "ranges": _rate_ranges(rates),
            "added": sorted(codes - current) if current else [], "removed": sorted(current - codes),
            "is_draft": snapshot.status == SnapshotStatus.DRAFT, "ctx": ctx,
        },
    )  # fmt: skip


@router.post("/dados/{snapshot_id}/{action}")
async def decide(
    request: Request,
    snapshot_id: uuid.UUID,
    action: str,
    ctx: ManagerCtx,
) -> Response:
    await verify_csrf(request)
    if action not in {"aprovar", "recusar"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        async with ctx.db() as session:
            if action == "aprovar":
                await approve_snapshot(
                    session, tenant_id=ctx.tenant_id, snapshot_id=snapshot_id,
                    user_id=ctx.user_id, now=ctx.now(),
                )  # fmt: skip
            else:
                await reject_snapshot(
                    session, tenant_id=ctx.tenant_id, snapshot_id=snapshot_id, user_id=ctx.user_id
                )
    except SnapshotStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return redirect("/painel/dados")


# --- Datas (bloqueios) ------------------------------------------------------------------


@router.get("/bloqueios", response_class=HTMLResponse)
async def blocks_page(request: Request, ctx: TenantCtx, error: str | None = None) -> Response:
    today = _today(ctx)
    async with ctx.db() as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        codes = sorted(await _codes(session, tenant.active_snapshot_id if tenant else None))
        blocks = list(
            (await session.execute(
                select(Block).where(Block.end_date > today)
                .order_by(Block.property_code, Block.start_date)
            )).scalars()
        )  # fmt: skip
    rows = [
        {
            "id": b.id, "code": b.property_code, "start": br_date(b.start_date),
            "end": br_date(b.end_date), "source": SOURCE_LABELS.get(b.source, b.source),
            "removable": b.source in REMOVABLE_SOURCES,
        }
        for b in blocks
    ]  # fmt: skip
    updated = tenant.availability_updated_at if tenant else None
    return render(
        request, "panel/blocks.html",
        {
            "rows": rows, "codes": codes, "today": today.isoformat(), "error": error,
            "updated": local_time(updated) if updated else None,
        },
    )  # fmt: skip


def _parse_period(form: dict[str, Any]) -> tuple[date, date]:
    try:
        start = date.fromisoformat(str(form.get("entrada", "")))
        end = date.fromisoformat(str(form.get("saida", "")))
    except ValueError as exc:
        raise ValueError("Informe as datas de entrada e saída.") from exc
    if end <= start:
        raise ValueError("A saída precisa ser depois da entrada (a noite da saída fica livre).")
    return start, end


@router.post("/bloqueios")
async def add_block(request: Request, ctx: TenantCtx) -> Response:
    form = await verify_csrf(request)
    code = str(form.get("codigo", "")).strip().upper()
    try:
        start, end = _parse_period(form)
    except ValueError as exc:
        return await blocks_page(request, ctx, error=str(exc))
    async with ctx.db() as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        if code not in await _codes(session, tenant.active_snapshot_id if tenant else None):
            return await blocks_page(
                request, ctx, error=f"Casa {code or '?'} não está na planilha aprovada."
            )
        block = Block(
            tenant_id=ctx.tenant_id, property_code=code, start_date=start, end_date=end,
            source=BlockSource.PANEL, created_by=ctx.user_id,
        )  # fmt: skip
        session.add(block)
        await session.flush()
        await append_audit(
            session, tenant_id=ctx.tenant_id, event_type="block_added",
            payload={"block_id": str(block.id), "codigo": code, "entrada": start.isoformat(),
                     "saida": end.isoformat(), "user_id": str(ctx.user_id), "via": "panel"},
        )  # fmt: skip
    return redirect("/painel/bloqueios")


@router.post("/bloqueios/{block_id}/remover")
async def remove_block(request: Request, block_id: uuid.UUID, ctx: TenantCtx) -> Response:
    await verify_csrf(request)
    async with ctx.db() as session:
        block = await session.get(Block, block_id)
        if block is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        if block.source not in REMOVABLE_SOURCES:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Esse bloqueio veio da planilha ou do calendário; altere na origem.",
            )
        await session.execute(delete(Block).where(Block.id == block_id))
        await append_audit(
            session, tenant_id=ctx.tenant_id, event_type="block_removed",
            payload={"block_id": str(block_id), "codigo": block.property_code,
                     "user_id": str(ctx.user_id)},
        )  # fmt: skip
    return redirect("/painel/bloqueios")


@router.post("/bloqueios/confirmar")
async def confirm_dates(request: Request, ctx: TenantCtx) -> Response:
    """RF16: "as datas estão certas" — renova a validade de 24 h (RF13)."""
    await verify_csrf(request)
    async with ctx.db() as session:
        tenant = await session.get(Tenant, ctx.tenant_id, with_for_update=True)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        tenant.availability_updated_at = ctx.now()
        await append_audit(
            session, tenant_id=ctx.tenant_id, event_type="availability_confirmed",
            payload={"user_id": str(ctx.user_id), "via": "panel"},
        )  # fmt: skip
    return redirect("/painel/bloqueios")
