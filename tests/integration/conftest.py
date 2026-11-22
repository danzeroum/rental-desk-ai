"""Banco real migrado; engines por papel (app sem BYPASSRLS, ops, dono)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.db import async_url
from app.core.rls import scoped_session_factory
from tests.conftest import (
    TEST_DATABASE_MIGRATION_URL,
    TEST_DATABASE_OPS_URL,
    TEST_DATABASE_URL,
)


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_MIGRATION_URL", TEST_DATABASE_MIGRATION_URL)
        command.upgrade(Config("alembic.ini"), "head")
    yield


def _engine(url: str) -> AsyncEngine:
    return create_async_engine(async_url(url), poolclass=NullPool)


@pytest.fixture
async def app_engine() -> AsyncIterator[AsyncEngine]:
    engine = _engine(TEST_DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def ops_engine() -> AsyncIterator[AsyncEngine]:
    engine = _engine(TEST_DATABASE_OPS_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def owner_engine() -> AsyncIterator[AsyncEngine]:
    engine = _engine(TEST_DATABASE_MIGRATION_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
def app_sessions(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return scoped_session_factory(app_engine)


@pytest.fixture
def ops_sessions(ops_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(ops_engine, expire_on_commit=False)
