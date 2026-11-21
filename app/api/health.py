"""Health checks: `/health/live` (processo de pé) e `/health` (dependências)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app import __version__
from app.core.db import ping_database
from app.core.logging import get_logger
from app.core.redis import ping_redis

router = APIRouter(tags=["health"])
log = get_logger(__name__)

CheckStatus = Literal["ok", "error"]


class HealthReport(BaseModel):
    status: CheckStatus
    version: str
    checks: dict[str, CheckStatus]


async def _check(name: str, probe: Callable[[], Awaitable[None]], limit: float) -> CheckStatus:
    try:
        async with asyncio.timeout(limit):
            await probe()
    except Exception as exc:  # o motivo vai para o log; a resposta pública não expõe detalhes
        log.warning("health_check_falhou", check=name, error_type=type(exc).__name__)
        return "error"
    return "ok"


@router.get("/health/live", response_model=HealthReport)
async def live() -> HealthReport:
    return HealthReport(status="ok", version=__version__, checks={})


@router.get(
    "/health",
    response_model=HealthReport,
    responses={503: {"model": HealthReport}},
)
async def ready(request: Request, response: Response) -> HealthReport:
    state = request.app.state
    timeout = state.settings.health_timeout_seconds
    db, redis = await asyncio.gather(
        _check("postgres", lambda: ping_database(state.db_engine), timeout),
        _check("redis", lambda: ping_redis(state.redis), timeout),
    )
    checks: dict[str, CheckStatus] = {"postgres": db, "redis": redis}
    overall: CheckStatus = "ok" if all(v == "ok" for v in checks.values()) else "error"
    if overall != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthReport(status=overall, version=__version__, checks=checks)
