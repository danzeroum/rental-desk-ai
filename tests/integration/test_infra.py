"""Integração com Postgres e Redis reais: health, papéis do banco e heartbeat."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.core.db import create_engine
from app.core.redis import create_redis
from app.core.service import heartbeat_key, is_alive, run_service
from app.main import create_app

pytestmark = pytest.mark.integration


def test_health_ok_with_real_dependencies(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200, response.text
    assert response.json()["checks"] == {"postgres": "ok", "redis": "ok"}


def test_health_503_when_redis_is_down(settings: Settings) -> None:
    broken = settings.model_copy(update={"redis_url": "redis://localhost:1/0"})
    with TestClient(create_app(broken)) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "error"


async def test_app_role_cannot_bypass_rls(settings: Settings) -> None:
    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
            ).one()
    finally:
        await engine.dispose()
    assert row.rolsuper is False
    assert row.rolbypassrls is False


async def test_service_keeps_heartbeat_and_cleans_up(settings: Settings) -> None:
    stop = asyncio.Event()
    redis = create_redis(settings)
    task = asyncio.create_task(run_service("teste", settings=settings, stop=stop))
    try:
        for _ in range(50):
            if await is_alive(redis, "teste"):
                break
            await asyncio.sleep(0.05)
        assert await is_alive(redis, "teste")
        ttl = await redis.ttl(heartbeat_key("teste"))
        assert 0 < ttl <= 30
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        assert not await is_alive(redis, "teste")
    finally:
        stop.set()
        await redis.aclose()


async def test_light_healthcheck(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import healthcheck  # noqa: PLC0415

    redis = create_redis(settings)
    await redis.delete(heartbeat_key("worker"))
    monkeypatch.setattr(healthcheck, "get_settings", lambda: settings)
    assert await healthcheck.check("worker") == 1
    await redis.set(heartbeat_key("worker"), "1", ex=30)
    assert await healthcheck.check("worker") == 0
    assert healthcheck.main(["banco"]) == 2
    await redis.delete(heartbeat_key("worker"))
    await redis.aclose()
