# ruff: noqa: F811 - fixtures `redis`, `keyring` e `tenant` vêm de test_webhook_flow
"""Falhas (SPEC seção 11): Redis e Postgres fora do ar; carga reduzida no CI.

A IA fora do ar é coberta pela contingência (conjunto de ouro, casos com `llm: down`).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.crypto import Keyring
from app.core.rls import tenant_session
from app.main import create_app
from app.models import Message
from app.scheduler.recovery import reschedule_stuck
from app.workers.processor import RecordingHandler
from app.workers.queue import WorkQueue
from app.workers.runner import Worker
from tests.integration.test_webhook_flow import (  # noqa: F401 - fixtures
    Sessions,
    _unused_notice,
    keyring,
    later_queue,
    message,
    post,
    processor,
    redis,
    tenant,
    webhook,
)
from tests.load.run_load import run as run_load

pytestmark = pytest.mark.integration
BROKEN_REDIS = "redis://localhost:1/0"


async def test_redis_down_on_webhook_then_retry_is_recovered(
    settings: Settings, redis: Redis, tenant: tuple[uuid.UUID, str], app_sessions: Sessions,
    ops_engine: AsyncEngine, keyring: Keyring,
) -> None:  # fmt: skip
    tenant_id, pnid = tenant
    payload = webhook(pnid, message(f"wamid.f{uuid.uuid4().hex}", "Casa para 4 de 10/12 a 15/12"))
    broken = settings.model_copy(update={"redis_url": BROKEN_REDIS})
    with TestClient(create_app(broken), raise_server_exceptions=False) as client:
        assert post(client, broken, payload) == 500  # gravou no banco, não conseguiu agendar
    with TestClient(create_app(settings)) as client:
        assert post(client, settings, payload) == 200  # reenvio da Meta: duplicado
    async with tenant_session(app_sessions, tenant_id) as session:
        stored = (await session.execute(select(Message))).scalars().all()
        assert len(stored) == 1 and stored[0].processed_at is None
        await session.execute(
            update(Message).values(created_at=datetime.now(UTC) - timedelta(minutes=2))
        )
    assert await redis.zcard("rd:debounce:due") == 0  # ninguém agendou
    assert await reschedule_stuck(ops_engine, WorkQueue(redis)) >= 1
    handler = RecordingHandler()
    worker = Worker(
        later_queue(redis),
        processor(app_sessions, later_queue(redis), keyring, handler),
        _unused_notice,
    )
    await worker.queue.ensure_groups()
    for _ in range(50):  # a varredura é global: pode reagendar conversas de outros testes
        if not await worker.tick(block_ms=100):
            break
    ours = [m.text for b in handler.batches if b.tenant_id == tenant_id for m in b.messages]
    assert ours == ["Casa para 4 de 10/12 a 15/12"]


async def test_postgres_down_on_webhook_and_health(
    settings: Settings, tenant: tuple[uuid.UUID, str]
) -> None:
    _, pnid = tenant
    broken = settings.model_copy(
        update={"database_url": "postgresql://rentaldesk_app:app_pw@localhost:1/rentaldesk_test"}
    )
    with TestClient(create_app(broken), raise_server_exceptions=False) as client:
        assert post(client, broken, webhook(pnid, message("wamid.pgdown"))) == 500  # a Meta reenvia
        health = client.get("/health")
    assert health.status_code == 503 and health.json()["checks"]["postgres"] == "error"


async def test_worker_survives_redis_outage(app_sessions: Sessions, keyring: Keyring) -> None:
    broken = Redis.from_url(BROKEN_REDIS, decode_responses=True)
    queue = WorkQueue(broken)
    worker = Worker(
        queue, processor(app_sessions, queue, keyring, RecordingHandler()), _unused_notice
    )
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    await asyncio.sleep(1.2)
    assert not task.done()  # tentando de novo, sem derrubar o processo
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    await broken.aclose()


async def test_load_smoke() -> None:
    """Versão curta do teste de carga (a completa: `make load`, resultados em docs/carga.md)."""
    result = await run_load(tenants=2, conversations=20, concurrency=8, quiet=0.5, llm=(0.05, 0.2))
    assert result.answered == 20 and result.dead_jobs == 0 and result.passed
