"""Ambiente do Alembic. Usa o papel dono do esquema (DATABASE_MIGRATION_URL)."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

target_metadata = None  # passa a apontar para os modelos a partir do M1


def _migration_url() -> str:
    url = os.environ.get("DATABASE_MIGRATION_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Defina DATABASE_MIGRATION_URL para rodar migrações")
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def run_migrations_offline() -> None:
    context.configure(url=_migration_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_migration_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
