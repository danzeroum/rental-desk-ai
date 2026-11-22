"""Engine assíncrona do banco (papel da aplicação, sem BYPASSRLS)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


def async_url(url: str) -> str:
    """Força o driver psycopg 3 (que atende sync e async) na URL do Postgres."""
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    raise ValueError("URL de banco precisa ser postgresql://")


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        async_url(str(settings.database_url)),
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
        connect_args={"application_name": "rental-desk-ai"},
    )


async def ping_database(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
