"""Relatório semanal (RF18): agrega a semana, grava em `weekly_report` e envia por e-mail.

O e-mail vai aos gestores ativos (comunicação do serviço contratado, sem dado de
hóspede); o resumo por WhatsApp é o RF32 (M10) e exigirá consentimento `weekly_report`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.mailer import Mailer, MailError
from app.adapters.whatsapp import GatewayError, WhatsAppGateway
from app.core.audit import append_audit
from app.core.config import TIMEZONE
from app.core.crypto import Keyring
from app.core.logging import get_logger
from app.core.rls import tenant_session
from app.domain import notices
from app.domain.report import (
    ConversationFacts,
    HandoffFacts,
    WeeklyMetrics,
    compute,
    email_text,
    week_before,
    whatsapp_params,
)
from app.models import AuditLog, Conversation, Handoff, Tenant, WeeklyReport
from app.models.enums import ConsentPurpose
from app.workers.brokers import manager_emails, managers_with_consent
from app.workers.channel import ChannelMissingError, channel_for
from app.workers.handoffs import hours_of

SEND_FROM: Final = time(8, 0)
SEND_UNTIL: Final = time(18, 0)
log = get_logger(__name__)
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def report_due(now: datetime) -> bool:
    local = now.astimezone(TIMEZONE)
    return local.weekday() == 0 and SEND_FROM <= local.time() < SEND_UNTIL


def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=TIMEZONE)


def offered_totals(payload: dict[str, Any]) -> list[int]:
    results = ((payload.get("search") or {}).get("results")) or []
    return [
        int(r["total_cents"])
        for r in results
        if isinstance(r, dict) and isinstance(r.get("total_cents"), int)
    ]


class ReportService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        mailer: Mailer,
        public_base_url: str,
        gateway: WhatsAppGateway | None = None,
        keyring: Keyring | None = None,
        clock: Clock = _utcnow,
    ) -> None:
        self.sessions, self.mailer, self.clock = sessions, mailer, clock
        self.gateway, self.keyring = gateway, keyring
        base = public_base_url.rstrip("/")
        self.panel_url = f"{base}/painel/passagens?show=sem-desfecho"
        self.results_url = f"{base}/painel/resultados"

    async def weekly(self, tenant_id: uuid.UUID) -> WeeklyMetrics | None:
        """Gera o relatório da semana anterior (uma vez) e envia; `None` se já existia."""
        period = week_before(self.clock().astimezone(TIMEZONE).date())
        async with tenant_session(self.sessions, tenant_id) as session:
            exists = await session.scalar(
                select(WeeklyReport.id).where(WeeklyReport.period_start == period[0])
            )
            tenant = await session.get(Tenant, tenant_id)
            if exists is not None or tenant is None:
                return None
            metrics = (await collect_period(session, tenant, period)).metrics
            session.add(
                WeeklyReport(
                    tenant_id=tenant_id, period_start=period[0], period_end=period[1],
                    metrics=metrics.to_json(),
                )
            )  # fmt: skip
            emails = await manager_emails(session, weekly_report=True)
            await append_audit(
                session, tenant_id=tenant_id, event_type="weekly_report",
                payload={"period_start": period[0].isoformat(), "recipients": len(emails)},
            )  # fmt: skip
            tenant_name = tenant.name
        subject, body = email_text(tenant_name, metrics, self.panel_url)
        for email in emails:
            try:
                await self.mailer.send(email, subject, body)
            except MailError:
                log.warning("relatorio_semanal_nao_enviado")
        await self._whatsapp(tenant_id, tenant_name, metrics)
        return metrics

    async def _whatsapp(
        self, tenant_id: uuid.UUID, tenant_name: str, metrics: WeeklyMetrics
    ) -> int:
        """RF32: resumo de 5 linhas por template, só a gestor com consentimento `weekly_report`."""
        if self.gateway is None or self.keyring is None:
            return 0
        async with tenant_session(self.sessions, tenant_id) as session:
            recipients = await managers_with_consent(session, ConsentPurpose.WEEKLY_REPORT)
            if not recipients:
                return 0
            try:
                channel = await channel_for(session, tenant_id, self.keyring)
            except ChannelMissingError:
                return 0
        params = whatsapp_params(tenant_name, metrics, self.results_url)
        sent = 0
        for person in recipients:
            try:
                await self.gateway.send_template(
                    channel,
                    person.phone_e164.removeprefix("+"),
                    notices.WEEKLY_SUMMARY.name,
                    params,
                )
                sent += 1
            except GatewayError as exc:
                log.warning("resumo_whatsapp_falhou", status=exc.status)
        async with tenant_session(self.sessions, tenant_id) as session:
            await append_audit(
                session, tenant_id=tenant_id, event_type="weekly_report_whatsapp",
                payload={"sent": sent, "period_start": metrics.period_start.isoformat()},
            )  # fmt: skip
        return sent


@dataclass(frozen=True)
class PeriodData:
    metrics: WeeklyMetrics
    first_contacts: list[datetime]
    """Primeira mensagem de cada conversa do período (para a série por dia do RF30)."""


async def collect_period(
    session: AsyncSession, tenant: Tenant, period: tuple[date, date]
) -> PeriodData:
    """Métricas de um período em dias locais (fim exclusivo); RF18 e RF30 usam a mesma conta."""
    start, end = _local_midnight(period[0]), _local_midnight(period[1])
    conversations = [
        ConversationFacts(str(c.contact_id), c.first_inbound_at, c.first_response_at)
        for c in (
            await session.execute(
                select(Conversation).where(
                    Conversation.first_inbound_at >= start, Conversation.first_inbound_at < end
                )
            )
        ).scalars()
        if c.first_inbound_at is not None
    ]
    handoffs = [
        HandoffFacts(h.created_at, h.assumed_at, h.outcome)
        for h in (
            await session.execute(
                select(Handoff).where(Handoff.created_at >= start, Handoff.created_at < end)
            )
        ).scalars()
    ]
    payloads = (
        await session.execute(
            select(AuditLog.payload).where(
                AuditLog.event_type == "response_sent",
                AuditLog.created_at >= start,
                AuditLog.created_at < end,
            )
        )
    ).scalars()
    totals = [total for payload in payloads for total in offered_totals(payload)]
    metrics = compute(period, conversations, handoffs, totals, hours_of(tenant))
    return PeriodData(metrics, [c.first_inbound_at for c in conversations])
