"""Montagem do processo `scheduler` a partir da configuração."""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.factory import build_gateway, build_keyring, build_mailer
from app.core.config import TIMEZONE, Settings, get_settings
from app.core.db import async_url, create_engine
from app.core.redis import create_redis
from app.core.rls import scoped_session_factory
from app.scheduler.quality import QUALITY_EVERY_SECONDS, refresh_quality, tenants_with_number
from app.scheduler.runner import Job, Scheduler, active_tenants, tenants_with_open_handoffs
from app.workers.handoffs import HandoffService
from app.workers.reminders import ReminderService, reminder_due
from app.workers.reports import ReportService, report_due

ESCALATION_EVERY_SECONDS = 60
REMINDER_CHECK_SECONDS = 300
REMINDER_KEY_SECONDS = 2 * 24 * 3600


async def send_daily_reminders(
    redis: Redis, ops_engine: AsyncEngine, service: ReminderService
) -> int:
    """Uma vez por dia e por imobiliária, entre 9h e 18h (trava no Redis com a data local)."""
    now = service.clock()
    if not reminder_due(now):
        return 0
    day = now.astimezone(TIMEZONE).date().isoformat()
    sent = 0
    for tenant_id in await active_tenants(ops_engine):
        if await redis.set(f"rd:reminder:{tenant_id}:{day}", "1", nx=True, ex=REMINDER_KEY_SECONDS):
            sent += await service.send(tenant_id)
    return sent


async def send_weekly_reports(ops_engine: AsyncEngine, service: ReportService) -> int:
    """Segunda, 8h-18h: um relatório por imobiliária e semana (o banco garante a unicidade)."""
    if not report_due(service.clock()):
        return 0
    sent = 0
    for tenant_id in await active_tenants(ops_engine):
        sent += await service.weekly(tenant_id) is not None
    return sent


async def run_scheduler(stop: asyncio.Event, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if settings.database_ops_url is None:
        raise RuntimeError("DATABASE_OPS_URL é obrigatória no scheduler (consulta global)")
    engine = create_engine(settings)
    ops_engine = create_async_engine(async_url(str(settings.database_ops_url)), poolclass=NullPool)
    redis = create_redis(settings)
    sessions, gateway, keyring = (
        scoped_session_factory(engine),
        build_gateway(settings),
        build_keyring(settings),
    )
    mailer = build_mailer(settings)
    service = HandoffService(sessions=sessions, gateway=gateway, keyring=keyring, mailer=mailer)

    async def escalate_all() -> int:
        actions = 0
        for tenant_id in await tenants_with_open_handoffs(ops_engine):
            actions += await service.escalate(tenant_id)
        return actions

    async def quality_all() -> int:
        tenants = await tenants_with_number(ops_engine)
        return sum([await refresh_quality(sessions, gateway, keyring, t) for t in tenants])

    reminders = ReminderService(
        sessions=sessions,
        gateway=gateway,
        keyring=keyring,
        public_base_url=settings.public_base_url,
    )

    reports = ReportService(
        sessions=sessions, mailer=mailer, public_base_url=settings.public_base_url
    )

    async def report_all() -> int:
        return await send_weekly_reports(ops_engine, reports)

    async def remind_all() -> int:
        return await send_daily_reminders(redis, ops_engine, reminders)

    jobs = [
        Job("escalonamento", ESCALATION_EVERY_SECONDS, escalate_all),
        Job("qualidade_numero", QUALITY_EVERY_SECONDS, quality_all),
        Job("lembrete_9h", REMINDER_CHECK_SECONDS, remind_all),
        Job("relatorio_semanal", REMINDER_CHECK_SECONDS, report_all),
    ]
    scheduler = Scheduler(redis, jobs)
    try:
        await scheduler.run(stop)
    finally:
        await redis.aclose()
        await engine.dispose()
        await ops_engine.dispose()
