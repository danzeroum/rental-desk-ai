"""Montagem do processo `scheduler` a partir da configuração."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.factory import build_gateway, build_keyring, build_mailer
from app.core.config import Settings, get_settings
from app.core.db import async_url, create_engine
from app.core.redis import create_redis
from app.core.rls import scoped_session_factory
from app.scheduler.quality import QUALITY_EVERY_SECONDS, refresh_quality, tenants_with_number
from app.scheduler.runner import Job, Scheduler, tenants_with_open_handoffs
from app.workers.handoffs import HandoffService

ESCALATION_EVERY_SECONDS = 60


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
    service = HandoffService(
        sessions=sessions, gateway=gateway, keyring=keyring, mailer=build_mailer(settings)
    )

    async def escalate_all() -> int:
        actions = 0
        for tenant_id in await tenants_with_open_handoffs(ops_engine):
            actions += await service.escalate(tenant_id)
        return actions

    async def quality_all() -> int:
        tenants = await tenants_with_number(ops_engine)
        return sum([await refresh_quality(sessions, gateway, keyring, t) for t in tenants])

    jobs = [
        Job("escalonamento", ESCALATION_EVERY_SECONDS, escalate_all),
        Job("qualidade_numero", QUALITY_EVERY_SECONDS, quality_all),
    ]
    scheduler = Scheduler(redis, jobs)
    try:
        await scheduler.run(stop)
    finally:
        await redis.aclose()
        await engine.dispose()
        await ops_engine.dispose()
