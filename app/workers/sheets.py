"""Planilha Google sincronizada (RF27).

A cada 15 min: lê a planilha; com erro, conta falha (3 seguidas → alerta). Sem
mudança, nada. Só bloqueios mudaram → entram direto (origem `google_sheet`). Casa,
tarifa ou regra mudou → nova versão como rascunho, que pede aprovação no painel
(nada de preço vai ao ar sem aprovação, RF15) e o gestor recebe um e-mail.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.google_sheet import SheetError, SheetSource
from app.adapters.link_checker import LinkChecker
from app.adapters.mailer import Mailer, MailError
from app.core.audit import append_audit
from app.core.config import TIMEZONE
from app.core.logging import bind_context, get_logger
from app.core.rls import tenant_session
from app.importer.analyze import analyze_workbook
from app.importer.service import (
    active_catalog_hash,
    blocks_json,
    prepare_import,
    replace_sheet_blocks,
    save_draft,
)
from app.models import DataSnapshot, SheetLink, Tenant
from app.models.enums import BlockSource, SnapshotSource, SnapshotStatus
from app.workers.brokers import manager_emails

ALERT_AFTER_FAILURES: Final = 3
MAX_ERRORS_SHOWN: Final = 3
log = get_logger(__name__)
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SyncOutcome(StrEnum):
    UNCHANGED = "unchanged"
    BLOCKS = "blocks"
    DRAFT = "draft"
    DRAFT_PENDING = "draft_pending"
    FAILED = "failed"


class SheetSyncService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        source: SheetSource,
        link_checker: LinkChecker,
        mailer: Mailer,
        public_base_url: str,
        clock: Clock = _utcnow,
    ) -> None:
        self.sessions, self.source, self.link_checker = sessions, source, link_checker
        self.mailer, self.clock = mailer, clock
        self.panel_url = f"{public_base_url.rstrip('/')}/painel/dados"

    async def sync(self, tenant_id: uuid.UUID) -> SyncOutcome | None:
        bind_context(tenant_id=str(tenant_id))
        now = self.clock()
        async with tenant_session(self.sessions, tenant_id) as session:
            link = await session.scalar(select(SheetLink))
            if link is None:
                return None
            sheet_id, last_hash = link.sheet_id, link.last_content_hash
        try:
            content = await self.source.fetch_xlsx(sheet_id)
        except SheetError as exc:
            return await self._failed(tenant_id, str(exc), now)
        today = now.astimezone(TIMEZONE).date()
        report = analyze_workbook(content, today=today)
        if not report.ok:
            errors = "; ".join(i.describe() for i in report.errors[:MAX_ERRORS_SHOWN])
            return await self._failed(tenant_id, f"a planilha tem erros: {errors}", now)
        data = report.data
        if data.content_hash() == last_hash:
            outcome = SyncOutcome.UNCHANGED
        else:
            outcome = await self._apply(tenant_id, data.catalog_hash(), content, now)
        async with tenant_session(self.sessions, tenant_id) as session:
            link = await session.scalar(select(SheetLink).with_for_update())
            if link is not None:
                link.last_attempt_at = link.last_success_at = now
                link.consecutive_failures, link.last_error, link.alerted_at = 0, None, None
                link.last_content_hash = data.content_hash()
        log.info("planilha_google_sincronizada", outcome=str(outcome))
        return outcome

    async def _apply(
        self, tenant_id: uuid.UUID, catalog_hash: str, content: bytes, now: datetime
    ) -> SyncOutcome:
        async with tenant_session(self.sessions, tenant_id) as session:
            tenant = await session.get(Tenant, tenant_id, with_for_update=True)
            active = (
                await session.get(DataSnapshot, tenant.active_snapshot_id)
                if tenant and tenant.active_snapshot_id
                else None
            )
            if active is not None and await active_catalog_hash(session, active) == catalog_hash:
                report = analyze_workbook(content, today=now.astimezone(TIMEZONE).date())
                blocks = blocks_json(report.data)
                await replace_sheet_blocks(
                    session, tenant_id, blocks, BlockSource.GOOGLE_SHEET, None
                )
                await append_audit(
                    session, tenant_id=tenant_id, event_type="sheet_blocks_synced",
                    payload={"blocks": len(blocks), "via": "google_sheet", "at": now.isoformat()},
                )  # fmt: skip
                return SyncOutcome.BLOCKS
        # Casa, tarifa ou regra mudou: rascunho para aprovação (com conferência dos links de fotos).
        report = await prepare_import(
            content, today=now.astimezone(TIMEZONE).date(), link_checker=self.link_checker
        )
        async with tenant_session(self.sessions, tenant_id) as session:
            pending = await session.scalar(
                select(DataSnapshot.id).where(
                    DataSnapshot.status == SnapshotStatus.DRAFT,
                    DataSnapshot.content_hash == report.data.content_hash(),
                )
            )
            if pending is not None:
                return SyncOutcome.DRAFT_PENDING
            draft = await save_draft(
                session, tenant_id=tenant_id, user_id=None, report=report,
                file_name="Planilha Google", source=SnapshotSource.GOOGLE_SHEET,
            )  # fmt: skip
            version, emails = draft.version, await manager_emails(session)
        for email in emails:
            try:
                await self.mailer.send(
                    email, f"Planilha Google: versão {version} aguarda aprovação",
                    "A planilha Google mudou em casa, tarifa ou regra. Nada vai ao ar sem a sua "
                    f"aprovação. Confira e aprove: {self.panel_url}\n",
                )  # fmt: skip
            except MailError:
                log.warning("aviso_rascunho_nao_enviado")
        return SyncOutcome.DRAFT

    async def _failed(self, tenant_id: uuid.UUID, error: str, now: datetime) -> SyncOutcome:
        async with tenant_session(self.sessions, tenant_id) as session:
            link = await session.scalar(select(SheetLink).with_for_update())
            if link is None:
                return SyncOutcome.FAILED
            link.last_attempt_at, link.last_error = now, error[:300]
            link.consecutive_failures += 1
            alert = link.consecutive_failures >= ALERT_AFTER_FAILURES and link.alerted_at is None
            if alert:
                link.alerted_at = now
                await append_audit(
                    session, tenant_id=tenant_id, event_type="sheet_sync_failing",
                    payload={"falhas": link.consecutive_failures, "erro": error[:300]},
                )  # fmt: skip
            emails = await manager_emails(session) if alert else []
        log.warning("planilha_google_falhou", error=error[:120])
        for email in emails:
            try:
                await self.mailer.send(
                    email, "Planilha Google sem sincronizar",
                    f"A planilha Google falhou {ALERT_AFTER_FAILURES} vezes seguidas: {error}.\n"
                    f"Confira no painel: {self.panel_url}\n",
                )  # fmt: skip
            except MailError:
                log.warning("alerta_planilha_nao_enviado")
        return SyncOutcome.FAILED
