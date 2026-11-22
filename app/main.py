"""Processo `app`: webhook, painel e API interna (FastAPI)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app import __version__
from app.adapters.factory import (
    build_fetcher,
    build_gateway,
    build_keyring,
    build_link_checker,
    build_mailer,
    build_sheet_source,
)
from app.api import health, webhook
from app.api.middleware import TraceMiddleware
from app.core.config import Settings, get_settings
from app.core.db import create_engine
from app.core.logging import configure_logging, get_logger
from app.core.redis import create_redis
from app.core.rls import scoped_session_factory
from app.web.auth import AuthService
from app.web.panel import install_panel
from app.workers.calendars import CalendarService
from app.workers.handoffs import HandoffService
from app.workers.queue import WorkQueue
from app.workers.sheets import SheetSyncService

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.db_engine = create_engine(settings)
        app.state.redis = create_redis(settings)
        app.state.sessions = scoped_session_factory(app.state.db_engine)
        app.state.queue = build_queue(app.state.redis, settings)
        app.state.keyring = build_keyring(settings)
        app.state.mailer = build_mailer(settings)
        app.state.auth = AuthService(
            sessions=app.state.sessions,
            redis=app.state.redis,
            keyring=app.state.keyring,
            mailer=app.state.mailer,
            public_base_url=settings.public_base_url,
        )
        app.state.handoffs = HandoffService(
            sessions=app.state.sessions,
            gateway=build_gateway(settings),
            keyring=app.state.keyring,
            mailer=app.state.mailer,
        )
        app.state.link_checker = build_link_checker(settings)
        app.state.calendars = CalendarService(
            sessions=app.state.sessions, fetcher=build_fetcher(settings), keyring=app.state.keyring,
            mailer=app.state.mailer, public_base_url=settings.public_base_url,
        )  # fmt: skip
        app.state.sheets = SheetSyncService(
            sessions=app.state.sessions, source=build_sheet_source(settings),
            link_checker=app.state.link_checker, mailer=app.state.mailer,
            public_base_url=settings.public_base_url,
        )  # fmt: skip
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
    app.include_router(webhook.router)
    install_panel(app)
    return app


def build_queue(redis: Redis, settings: Settings) -> WorkQueue:
    return WorkQueue(
        redis,
        quiet_seconds=settings.debounce_quiet_seconds,
        max_seconds=settings.debounce_max_seconds,
        rate_limit_per_minute=settings.rate_limit_per_minute,
        silence_seconds=settings.rate_limit_silence_seconds,
    )
