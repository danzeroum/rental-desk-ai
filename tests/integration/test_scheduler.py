"""Scheduler: trava por tarefa, intervalo e consulta global com o papel de operação."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.rls import tenant_session
from app.models import Handoff
from app.scheduler.runner import Job, Scheduler, tenants_with_open_handoffs
from tests.factories import create_tenant, create_user, seed_tenant_data

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_jobs_run_on_interval_and_survive_errors(redis: Redis) -> None:
    calls: list[str] = []

    async def ok() -> int:
        calls.append("ok")
        return 1

    async def boom() -> int:
        calls.append("boom")
        raise RuntimeError("falha simulada")

    clock = Clock()
    scheduler = Scheduler(redis, [Job("a", 60, ok), Job("b", 60, boom)], clock=clock)
    assert await scheduler.tick() == ["a", "b"]
    clock.now += 30
    assert await scheduler.tick() == []
    clock.now += 31
    await redis.flushdb()  # trava expira com o intervalo
    assert await scheduler.tick() == ["a", "b"]
    assert calls == ["ok", "boom", "ok", "boom"]


async def test_lock_prevents_two_schedulers(redis: Redis) -> None:
    calls: list[int] = []

    async def job() -> int:
        calls.append(1)
        return 0

    first = Scheduler(redis, [Job("x", 60, job)])
    second = Scheduler(redis, [Job("x", 60, job)])
    await first.tick()
    await second.tick()
    assert calls == [1]


async def test_ops_role_lists_tenants_with_open_handoffs(
    ops_engine: AsyncEngine,
    app_sessions: async_sessionmaker[AsyncSession],
    ops_sessions: async_sessionmaker[AsyncSession],
) -> None:
    with_open = await create_tenant(ops_sessions)
    closed = await create_tenant(ops_sessions)
    for tenant in (with_open, closed):
        await seed_tenant_data(app_sessions, tenant, await create_user(app_sessions))
    async with tenant_session(app_sessions, closed) as session:
        handoff = (await session.execute(select(Handoff))).scalar_one()
        handoff.status = "closed"
    tenants = await tenants_with_open_handoffs(ops_engine)
    assert with_open in tenants
    assert closed not in tenants
