"""Importação e aprovação no banco (M2): rascunho, aprovação registrada, bloqueios."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.link_checker import FakeLinkChecker
from app.core.audit import first_broken_seq
from app.core.rls import tenant_session
from app.importer.model import BlockData
from app.importer.sample import PHOTOS_BASE, build_sample
from app.importer.service import (
    SnapshotStateError,
    approve_snapshot,
    prepare_import,
    reject_snapshot,
    save_draft,
)
from app.importer.workbook import build_workbook
from app.models import AuditLog, Block, DataSnapshot, Property, Rate, Tenant
from app.models.enums import BlockSource, SnapshotStatus
from tests.factories import create_tenant, create_user

pytestmark = pytest.mark.integration

TODAY = date(2026, 11, 21)
NOW = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)

Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
async def ids(app_sessions: Sessions, ops_sessions: Sessions) -> tuple[uuid.UUID, uuid.UUID]:
    return await create_tenant(ops_sessions), await create_user(app_sessions)


async def _import(
    app: Sessions, tenant: uuid.UUID, user: uuid.UUID, **sample_changes: object
) -> uuid.UUID:
    sample = build_sample(2026)
    for key, value in sample_changes.items():
        setattr(sample, key, value)
    report = await prepare_import(
        build_workbook(sample), today=TODAY, link_checker=FakeLinkChecker()
    )
    async with tenant_session(app, tenant, user_id=user) as session:
        snapshot = await save_draft(
            session, tenant_id=tenant, user_id=user, report=report, file_name="planilha.xlsx"
        )
        return snapshot.id


async def test_import_creates_draft_without_going_live(
    app_sessions: Sessions, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant, user = ids
    snapshot_id = await _import(app_sessions, tenant, user)
    async with tenant_session(app_sessions, tenant) as session:
        snapshot = await session.get(DataSnapshot, snapshot_id)
        assert snapshot is not None
        assert (snapshot.version, snapshot.status) == (1, SnapshotStatus.DRAFT)
        assert len(snapshot.content_hash) == 64
        assert snapshot.rules["check-in"] == "a partir das 14h"
        assert len(snapshot.blocks) == 3
        assert await session.scalar(select(func.count()).select_from(Property)) == 10
        assert await session.scalar(select(func.count()).select_from(Rate)) == 60
        tenant_row = await session.get(Tenant, tenant)
        assert tenant_row is not None
        assert tenant_row.active_snapshot_id is None  # nada no ar sem aprovação
        assert await session.scalar(select(func.count()).select_from(Block)) == 0
        events = (await session.execute(select(AuditLog.event_type))).scalars().all()
        assert events == ["snapshot_imported"]


async def test_versions_increment(app_sessions: Sessions, ids: tuple[uuid.UUID, uuid.UUID]) -> None:
    tenant, user = ids
    first, second = (
        await _import(app_sessions, tenant, user),
        await _import(app_sessions, tenant, user),
    )
    async with tenant_session(app_sessions, tenant) as session:
        versions = [(await session.get(DataSnapshot, s)).version for s in (first, second)]  # type: ignore[union-attr]
    assert versions == [1, 2]


async def test_broken_photo_link_becomes_warning(
    app_sessions: Sessions, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant, user = ids
    broken = f"{PHOTOS_BASE}/casa04"
    report = await prepare_import(
        build_workbook(build_sample(2026)), today=TODAY, link_checker=FakeLinkChecker([broken])
    )
    assert report.ok
    assert [w.message for w in report.warnings] == [
        "CASA04: link de fotos fora do ar (respondeu com erro 404)"
    ]
    async with tenant_session(app_sessions, tenant) as session:
        snapshot = await save_draft(
            session, tenant_id=tenant, user_id=user, report=report, file_name=None
        )
        assert snapshot.warnings[0]["column"] == "link_fotos"


async def test_report_with_errors_is_not_saved(
    app_sessions: Sessions, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant, user = ids
    report = await prepare_import(build_workbook(), today=TODAY, link_checker=FakeLinkChecker())
    with pytest.raises(SnapshotStateError, match="erros"):
        async with tenant_session(app_sessions, tenant) as session:
            await save_draft(session, tenant_id=tenant, user_id=user, report=report, file_name=None)


async def test_approval_records_who_when_and_hash(
    app_sessions: Sessions, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant, user = ids
    snapshot_id = await _import(app_sessions, tenant, user)
    async with tenant_session(app_sessions, tenant, user_id=user) as session:
        await approve_snapshot(
            session, tenant_id=tenant, snapshot_id=snapshot_id, user_id=user, now=NOW
        )
    async with tenant_session(app_sessions, tenant) as session:
        snapshot = await session.get(DataSnapshot, snapshot_id)
        tenant_row = await session.get(Tenant, tenant)
        assert snapshot is not None and tenant_row is not None
        assert (snapshot.status, snapshot.approved_by, snapshot.approved_at) == (
            SnapshotStatus.APPROVED, user, NOW,
        )  # fmt: skip
        assert tenant_row.active_snapshot_id == snapshot_id
        assert tenant_row.availability_updated_at == NOW
        blocks = (await session.execute(select(Block))).scalars().all()
        assert {(b.property_code, b.source) for b in blocks} == {
            ("CASA03", BlockSource.SPREADSHEET),
            ("CASA05", BlockSource.SPREADSHEET),
            ("CASA09", BlockSource.SPREADSHEET),
        }
        approved = (
            await session.execute(
                select(AuditLog).where(AuditLog.event_type == "snapshot_approved")
            )
        ).scalar_one()
        assert approved.snapshot_hash == snapshot.content_hash
        assert approved.payload["approved_by"] == str(user)
        assert await first_broken_seq(session, tenant) is None


async def test_new_approval_supersedes_and_replaces_only_spreadsheet_blocks(
    app_sessions: Sessions, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant, user = ids
    first = await _import(app_sessions, tenant, user)
    async with tenant_session(app_sessions, tenant, user_id=user) as session:
        await approve_snapshot(session, tenant_id=tenant, snapshot_id=first, user_id=user, now=NOW)
        session.add(
            Block(
                tenant_id=tenant, property_code="CASA01", start_date=date(2027, 1, 5),
                end_date=date(2027, 1, 8), source=BlockSource.PANEL,
            )
        )  # fmt: skip
    second = await _import(
        app_sessions,
        tenant,
        user,
        blocks=[BlockData("CASA02", date(2027, 1, 10), date(2027, 1, 12))],
    )
    async with tenant_session(app_sessions, tenant, user_id=user) as session:
        await approve_snapshot(session, tenant_id=tenant, snapshot_id=second, user_id=user, now=NOW)
    async with tenant_session(app_sessions, tenant) as session:
        statuses = {s.id: s.status for s in (await session.execute(select(DataSnapshot))).scalars()}
        blocks = {
            (b.property_code, b.source) for b in (await session.execute(select(Block))).scalars()
        }
    assert statuses == {first: SnapshotStatus.SUPERSEDED, second: SnapshotStatus.APPROVED}
    assert blocks == {("CASA01", BlockSource.PANEL), ("CASA02", BlockSource.SPREADSHEET)}


async def test_cannot_approve_twice_or_after_rejection(
    app_sessions: Sessions, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant, user = ids
    approved, rejected = (
        await _import(app_sessions, tenant, user),
        await _import(app_sessions, tenant, user),
    )
    async with tenant_session(app_sessions, tenant, user_id=user) as session:
        await approve_snapshot(
            session, tenant_id=tenant, snapshot_id=approved, user_id=user, now=NOW
        )
        await reject_snapshot(session, tenant_id=tenant, snapshot_id=rejected, user_id=user)
    for snapshot_id in (approved, rejected):
        with pytest.raises(SnapshotStateError, match="não está aguardando aprovação"):
            async with tenant_session(app_sessions, tenant, user_id=user) as session:
                await approve_snapshot(
                    session, tenant_id=tenant, snapshot_id=snapshot_id, user_id=user, now=NOW
                )


async def test_cannot_approve_other_tenant_snapshot(
    app_sessions: Sessions, ops_sessions: Sessions, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant, user = ids
    foreign = await _import(app_sessions, tenant, user)
    other = await create_tenant(ops_sessions)
    with pytest.raises(SnapshotStateError, match="não encontrada"):
        async with tenant_session(app_sessions, other, user_id=user) as session:
            await approve_snapshot(
                session, tenant_id=other, snapshot_id=foreign, user_id=user, now=NOW
            )
