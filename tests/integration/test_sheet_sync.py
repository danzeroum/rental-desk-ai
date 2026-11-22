# ruff: noqa: F811 - a fixture `keyring` vem de test_pipeline
"""Planilha Google (RF27): bloqueios entram direto; mudança de preço pede aprovação."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import select

from app.adapters.google_sheet import FakeSheetSource, SheetError
from app.adapters.link_checker import FakeLinkChecker
from app.adapters.mailer import FakeMailer
from app.core.crypto import Keyring
from app.core.rls import tenant_session
from app.domain.catalog import BlockData
from app.importer.sample import build_sample
from app.importer.service import approve_snapshot
from app.importer.workbook import build_workbook
from app.models import Block, DataSnapshot, SheetLink, Tenant
from app.models.enums import BlockSource, MemberRole, SnapshotSource, SnapshotStatus
from app.workers.sheets import SheetSyncService, SyncOutcome
from tests.integration.test_handoffs import add_member
from tests.integration.test_pipeline import NOW, Sessions, keyring, setup_tenant  # noqa: F401

pytestmark = pytest.mark.integration
SHEET = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


async def _linked(
    app: Sessions, ops: Sessions, keyring: Keyring
) -> tuple[uuid.UUID, FakeSheetSource, FakeMailer, SheetSyncService, uuid.UUID]:
    tenant_id, _ = await setup_tenant(app, ops, keyring)
    gestor = await add_member(app, tenant_id, "Gil", "+5513988772200", MemberRole.GESTOR)
    async with tenant_session(app, tenant_id) as session:
        session.add(SheetLink(tenant_id=tenant_id, sheet_id=SHEET))
    source, mailer = FakeSheetSource({SHEET: build_workbook(build_sample(2026))}), FakeMailer()
    service = SheetSyncService(
        sessions=app, source=source, link_checker=FakeLinkChecker(), mailer=mailer,
        public_base_url="https://painel", clock=lambda: NOW,
    )  # fmt: skip
    return tenant_id, source, mailer, service, gestor


async def test_blocks_go_live_and_price_change_waits_for_approval(
    app_sessions: Sessions, ops_sessions: Sessions, keyring: Keyring
) -> None:
    tenant_id, source, mailer, service, gestor = await _linked(app_sessions, ops_sessions, keyring)
    assert await service.sync(tenant_id) == SyncOutcome.BLOCKS  # mesma planilha: só os bloqueios
    assert await service.sync(tenant_id) == SyncOutcome.UNCHANGED

    data = build_sample(2026)
    new_block = BlockData("CASA07", date(2027, 2, 1), date(2027, 2, 5))
    source.sheets[SHEET] = build_workbook(replace(data, blocks=[*data.blocks, new_block]))
    assert await service.sync(tenant_id) == SyncOutcome.BLOCKS
    async with tenant_session(app_sessions, tenant_id) as session:
        blocks = list(
            (await session.execute(select(Block).where(Block.property_code == "CASA07"))).scalars()
        )
        tenant = await session.get(Tenant, tenant_id)
    assert [(b.start_date, b.source) for b in blocks] == [
        (date(2027, 2, 1), BlockSource.GOOGLE_SHEET)
    ]
    active_before = tenant.active_snapshot_id  # type: ignore[union-attr]

    pricier = [
        replace(r, daily_cents=r.daily_cents + 10_000) if r.property_code == "CASA01" else r
        for r in data.rates
    ]
    source.sheets[SHEET] = build_workbook(replace(data, rates=pricier))
    assert await service.sync(tenant_id) == SyncOutcome.DRAFT
    assert await service.sync(tenant_id) == SyncOutcome.UNCHANGED  # mesma leitura: nada de novo
    async with tenant_session(app_sessions, tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        draft = await session.scalar(
            select(DataSnapshot).where(DataSnapshot.status == SnapshotStatus.DRAFT)
        )
    assert tenant is not None and tenant.active_snapshot_id == active_before  # preço não foi ao ar
    assert draft is not None and draft.source == SnapshotSource.GOOGLE_SHEET
    assert any("aguarda aprovação" in m.subject for m in mailer.sent)

    async with tenant_session(app_sessions, tenant_id) as session:
        await approve_snapshot(
            session, tenant_id=tenant_id, snapshot_id=draft.id, user_id=gestor, now=NOW
        )
    async with tenant_session(app_sessions, tenant_id) as session:
        sources = set(
            (
                await session.execute(select(Block.source).where(Block.source != BlockSource.ICAL))
            ).scalars()
        )
    assert sources == {BlockSource.GOOGLE_SHEET}


async def test_errors_and_access_failures_alert_after_three(
    app_sessions: Sessions, ops_sessions: Sessions, keyring: Keyring
) -> None:
    tenant_id, source, mailer, service, _ = await _linked(app_sessions, ops_sessions, keyring)
    source.sheets[SHEET] = b"nao e xlsx"
    assert await service.sync(tenant_id) == SyncOutcome.FAILED
    source.sheets[SHEET] = SheetError("planilha não encontrada ou não compartilhada com sa@x")
    for _ in range(3):
        await service.sync(tenant_id)
    assert len([m for m in mailer.sent if "sem sincronizar" in m.subject]) == 1
    async with tenant_session(app_sessions, tenant_id) as session:
        link = await session.scalar(select(SheetLink))
    assert (
        link is not None
        and link.consecutive_failures == 4
        and "compartilhada" in (link.last_error or "")
    )
