"""Importação e aprovação da planilha (RF14, RF15).

Fluxo: `prepare_import` (lê, valida e confere links, sem banco) →
`save_draft` (grava a versão como rascunho) → `approve_snapshot` ou
`reject_snapshot`. Nada vai ao ar sem aprovação registrada: só a aprovação muda
`tenant.active_snapshot_id` e os bloqueios vindos da planilha.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, fields
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.link_checker import LinkChecker
from app.core.audit import append_audit
from app.core.logging import get_logger
from app.importer.analyze import analyze_workbook
from app.importer.model import ImportReport, Issue, PropertyData, RateData, Severity, WorkbookData
from app.importer.schema import IMOVEIS
from app.models import Block, DataSnapshot, Property, Rate, Tenant
from app.models.enums import BlockSource, SnapshotSource, SnapshotStatus

log = get_logger(__name__)


class SnapshotStateError(Exception):
    """Ação incompatível com o estado da versão; a mensagem vai para o painel."""


async def prepare_import(content: bytes, *, today: date, link_checker: LinkChecker) -> ImportReport:
    """Valida o arquivo e confere os links de fotos (aviso, não bloqueia). Não usa o banco."""
    report = analyze_workbook(content, today=today)
    if not report.ok:
        return report
    urls = [p.photos_url for p in report.data.properties if p.photos_url]
    if urls:
        statuses = await link_checker.check(urls)
        for prop in report.data.properties:
            status = statuses.get(prop.photos_url or "")
            if status is not None and not status.ok:
                report.issues.append(
                    Issue(
                        Severity.WARNING,
                        IMOVEIS.name,
                        f"{prop.code}: link de fotos fora do ar ({status.detail})",
                        column="link_fotos",
                    )
                )
    return report


def _issue_json(issue: Issue) -> dict[str, Any]:
    return {k: v for k, v in asdict(issue).items() if v is not None}


async def _lock_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    """Serializa importações e aprovações da mesma imobiliária."""
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    ).scalar_one()
    return tenant


async def save_draft(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    report: ImportReport,
    file_name: str | None,
    source: SnapshotSource = SnapshotSource.UPLOAD,
) -> DataSnapshot:
    """Grava a versão como rascunho. Recusa relatório com erros."""
    if not report.ok:
        raise SnapshotStateError("a planilha tem erros; corrija e envie de novo")
    await _lock_tenant(session, tenant_id)
    last = await session.scalar(
        select(func.max(DataSnapshot.version)).where(DataSnapshot.tenant_id == tenant_id)
    )
    data = report.data
    snapshot = DataSnapshot(
        tenant_id=tenant_id,
        version=(last or 0) + 1,
        source=source,
        status=SnapshotStatus.DRAFT,
        content_hash=data.content_hash(),
        file_name=file_name,
        rules=data.rules,
        warnings=[_issue_json(i) for i in report.warnings],
        blocks=blocks_json(data),
        created_by=user_id,
    )
    session.add(snapshot)
    await session.flush()
    session.add_all(
        Property(tenant_id=tenant_id, snapshot_id=snapshot.id, **asdict(p)) for p in data.properties
    )
    session.add_all(
        Rate(tenant_id=tenant_id, snapshot_id=snapshot.id, **asdict(r)) for r in data.rates
    )
    await append_audit(
        session,
        tenant_id=tenant_id,
        event_type="snapshot_imported",
        snapshot_hash=snapshot.content_hash,
        payload={
            "version": snapshot.version,
            "source": str(source),
            "user_id": str(user_id) if user_id else None,
            "properties": len(data.properties),
            "rates": len(data.rates),
            "blocks": len(data.blocks),
            "warnings": len(report.warnings),
        },
    )
    log.info("snapshot_importado", version=snapshot.version, warnings=len(report.warnings))
    return snapshot


async def _draft(
    session: AsyncSession, tenant_id: uuid.UUID, snapshot_id: uuid.UUID
) -> DataSnapshot:
    snapshot = await session.get(DataSnapshot, snapshot_id, with_for_update=True)
    if snapshot is None or snapshot.tenant_id != tenant_id:
        raise SnapshotStateError("versão não encontrada")
    if snapshot.status != SnapshotStatus.DRAFT:
        raise SnapshotStateError(f"a versão {snapshot.version} não está aguardando aprovação")
    return snapshot


async def approve_snapshot(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
) -> DataSnapshot:
    """Põe a versão no ar: registra quem e quando, troca a versão ativa e os bloqueios da
    planilha, e conta como atualização das datas (RF13)."""
    tenant = await _lock_tenant(session, tenant_id)
    snapshot = await _draft(session, tenant_id, snapshot_id)
    if tenant.active_snapshot_id is not None:
        previous = await session.get(DataSnapshot, tenant.active_snapshot_id)
        if previous is not None:
            previous.status = SnapshotStatus.SUPERSEDED
    snapshot.status = SnapshotStatus.APPROVED
    snapshot.approved_by = user_id
    snapshot.approved_at = now
    tenant.active_snapshot_id = snapshot.id
    tenant.availability_updated_at = now
    await _replace_spreadsheet_blocks(session, tenant_id, snapshot, user_id)
    await session.flush()
    await append_audit(
        session,
        tenant_id=tenant_id,
        event_type="snapshot_approved",
        snapshot_hash=snapshot.content_hash,
        payload={
            "version": snapshot.version,
            "snapshot_id": str(snapshot.id),
            "approved_by": str(user_id),
            "approved_at": now.isoformat(),
        },
    )
    log.info("snapshot_aprovado", version=snapshot.version)
    return snapshot


async def _replace_spreadsheet_blocks(
    session: AsyncSession, tenant_id: uuid.UUID, snapshot: DataSnapshot, user_id: uuid.UUID | None
) -> None:
    """A planilha aprovada (upload ou Google) é a fonte única dos bloqueios de planilha."""
    source = (
        BlockSource.GOOGLE_SHEET
        if snapshot.source == SnapshotSource.GOOGLE_SHEET
        else BlockSource.SPREADSHEET
    )
    await replace_sheet_blocks(session, tenant_id, snapshot.blocks, source, user_id)


async def replace_sheet_blocks(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    blocks: list[Any],
    source: BlockSource,
    user_id: uuid.UUID | None,
) -> None:
    await session.execute(
        delete(Block).where(
            Block.tenant_id == tenant_id,
            Block.source.in_([BlockSource.SPREADSHEET, BlockSource.GOOGLE_SHEET]),
        )
    )
    session.add_all(
        Block(
            tenant_id=tenant_id,
            property_code=b["codigo"],
            start_date=date.fromisoformat(b["start_date"]),
            end_date=date.fromisoformat(b["end_date"]),
            source=source,
            created_by=user_id,
        )
        for b in blocks
    )


def blocks_json(data: WorkbookData) -> list[dict[str, str]]:
    return [
        {
            "codigo": b.property_code,
            "start_date": b.start_date.isoformat(),
            "end_date": b.end_date.isoformat(),
        }
        for b in data.blocks
    ]


async def active_catalog_hash(session: AsyncSession, snapshot: DataSnapshot) -> str:
    """Hash do catálogo (sem bloqueios) da versão aprovada, montado do banco."""
    props = (
        await session.execute(select(Property).where(Property.snapshot_id == snapshot.id))
    ).scalars()
    rates = (await session.execute(select(Rate).where(Rate.snapshot_id == snapshot.id))).scalars()
    data = WorkbookData(
        properties=[
            PropertyData(**{f.name: getattr(p, f.name) for f in fields(PropertyData)})
            for p in props
        ],
        rates=[RateData(**{f.name: getattr(r, f.name) for f in fields(RateData)}) for r in rates],
        rules=dict(snapshot.rules or {}),
    )
    return data.catalog_hash()


async def reject_snapshot(
    session: AsyncSession, *, tenant_id: uuid.UUID, snapshot_id: uuid.UUID, user_id: uuid.UUID
) -> DataSnapshot:
    await _lock_tenant(session, tenant_id)
    snapshot = await _draft(session, tenant_id, snapshot_id)
    snapshot.status = SnapshotStatus.REJECTED
    await session.flush()
    await append_audit(
        session,
        tenant_id=tenant_id,
        event_type="snapshot_rejected",
        snapshot_hash=snapshot.content_hash,
        payload={"version": snapshot.version, "rejected_by": str(user_id)},
    )
    return snapshot
