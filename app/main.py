"""Processo `app`: webhook, painel e API interna (FastAPI)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import health
from app.api.middleware import TraceMiddleware
from app.core.config import Settings, get_settings
from app.core.db import create_engine
from app.core.logging import configure_logging, get_logger
from app.core.redis import create_redis

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.db_engine = create_engine(settings)
        app.state.redis = create_redis(settings)
        log.info("app_iniciado", version=__version__, env=settings.env.value)
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.db_engine.dispose()
            log.info("app_encerrado")

    app = FastAPI(
        title="rental-desk-ai",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production_like else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production_like else "/openapi.json",
    )
    app.add_middleware(TraceMiddleware)
    app.include_router(health.router)
    return app
