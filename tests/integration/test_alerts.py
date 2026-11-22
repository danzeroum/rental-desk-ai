"""Alertas ao operador: plataforma (fila, disco, backup) e imobiliárias, sem repetir."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.mailer import FakeMailer
from app.core.config import Settings
from app.core.rls import global_session
from app.models import AppUser
from app.scheduler.alerts import HEARTBEAT_KEY, AlertService
from app.workers.queue import STREAM_NORMAL
from tests.factories import create_tenant

pytestmark = pytest.mark.integration
Sessions = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 12, 20, 15, 0, tzinfo=UTC)
OPERATOR = f"op-alertas-{uuid.uuid4().hex[:8]}@exemplo.com"


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_alerts_email_once_and_cover_platform_and_tenants(
    redis: Redis, app_sessions: Sessions, ops_sessions: Sessions, ops_engine: AsyncEngine
) -> None:
    await create_tenant(ops_sessions, "Imobiliária Sem Planilha")  # ativa e sem dados: erro
    async with global_session(app_sessions) as session:
        session.add(AppUser(email=OPERATOR, display_name="Op", is_operator=True))
    for _ in range(120):
        await redis.xadd(STREAM_NORMAL, {"kind": "conversation", "key": "x:y"})
    mailer = FakeMailer()
    service = AlertService(
        sessions=app_sessions, ops_engine=ops_engine, redis=redis, mailer=mailer,
        public_base_url="https://painel", clock=lambda: NOW,
    )  # fmt: skip
    assert await service.run() >= 3
    [mail] = [m for m in mailer.sent if m.to == OPERATOR]
    assert "120 tarefas acumuladas" in mail.body and "nenhum backup" in mail.body
    assert "Imobiliária Sem Planilha: sem planilha aprovada" in mail.body
    assert "https://painel/operador" in mail.body and "erro(s)" in mail.subject
    assert await service.run() == 0  # mesmo alerta não repete
    await redis.set(
        HEARTBEAT_KEY,
        json.dumps({"at": int((NOW - timedelta(hours=1)).timestamp()), "remote": False}),
    )
    platform = await service.platform()
    assert platform.backup_remote is False and platform.backup_at is not None
    assert await service.run() == 1  # backup ok, mas só local: alerta novo de cópia externa
    assert "sem cópia externa" in mailer.sent[-1].body
