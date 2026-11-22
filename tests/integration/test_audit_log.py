"""Trilha de auditoria: só inclusão, encadeada por hash, detecta adulteração."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.audit import append_audit, first_broken_seq
from app.core.rls import tenant_session
from app.models import AuditLog, PrivacyNotice
from tests.factories import create_tenant

pytestmark = pytest.mark.integration

GENESIS = b"\x00" * 32


@pytest.fixture
async def tenant(ops_sessions: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    return await create_tenant(ops_sessions)


async def _append(app: async_sessionmaker[AsyncSession], tenant: uuid.UUID, n: int) -> None:
    for i in range(n):
        async with tenant_session(app, tenant) as session:
            await append_audit(session, tenant_id=tenant, event_type="teste", payload={"i": i})


async def _entries(app: async_sessionmaker[AsyncSession], tenant: uuid.UUID) -> list[AuditLog]:
    async with tenant_session(app, tenant) as session:
        return list((await session.execute(select(AuditLog).order_by(AuditLog.seq))).scalars())


async def test_entries_are_chained(
    app_sessions: async_sessionmaker[AsyncSession], tenant: uuid.UUID
) -> None:
    await _append(app_sessions, tenant, 3)
    entries = await _entries(app_sessions, tenant)
    assert [e.seq for e in entries] == [1, 2, 3]
    assert entries[0].prev_hash == GENESIS
    assert entries[1].prev_hash == entries[0].hash
    assert entries[2].prev_hash == entries[1].hash
    assert all(len(e.hash) == 32 for e in entries)
    async with tenant_session(app_sessions, tenant) as session:
        assert await first_broken_seq(session, tenant) is None


async def test_client_cannot_forge_chain_fields(
    app_sessions: async_sessionmaker[AsyncSession], tenant: uuid.UUID
) -> None:
    async with tenant_session(app_sessions, tenant) as session:
        session.add(
            AuditLog(
                tenant_id=tenant,
                event_type="forjado",
                payload={},
                seq=999,
                prev_hash=b"x",
                hash=b"y",
            )
        )
    (entry,) = await _entries(app_sessions, tenant)
    assert entry.seq == 1
    assert entry.prev_hash == GENESIS
    assert entry.hash != b"y"


async def test_chains_are_independent_per_tenant(
    app_sessions: async_sessionmaker[AsyncSession],
    ops_sessions: async_sessionmaker[AsyncSession],
    tenant: uuid.UUID,
) -> None:
    other = await create_tenant(ops_sessions)
    await _append(app_sessions, tenant, 2)
    await _append(app_sessions, other, 1)
    (entry,) = await _entries(app_sessions, other)
    assert (entry.seq, entry.prev_hash) == (1, GENESIS)


async def test_concurrent_appends_keep_a_single_chain(
    app_sessions: async_sessionmaker[AsyncSession], tenant: uuid.UUID
) -> None:
    async def one(i: int) -> None:
        async with tenant_session(app_sessions, tenant) as session:
            await append_audit(
                session, tenant_id=tenant, event_type="concorrente", payload={"i": i}
            )

    await asyncio.gather(*(one(i) for i in range(20)))
    entries = await _entries(app_sessions, tenant)
    assert [e.seq for e in entries] == list(range(1, 21))
    async with tenant_session(app_sessions, tenant) as session:
        assert await first_broken_seq(session, tenant) is None


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_log SET payload = '{}'", "DELETE FROM audit_log", "TRUNCATE audit_log"],
)
async def test_runtime_roles_cannot_change_history(
    app_sessions: async_sessionmaker[AsyncSession], tenant: uuid.UUID, statement: str
) -> None:
    await _append(app_sessions, tenant, 1)
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with tenant_session(app_sessions, tenant) as session:
            await session.execute(text(statement))


async def test_even_schema_owner_cannot_update_or_delete(
    app_sessions: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine, tenant: uuid.UUID
) -> None:
    await _append(app_sessions, tenant, 1)
    for statement in ("UPDATE audit_log SET payload = '{}'", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError, match="somente inclusão"):
            async with owner_engine.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('app.current_tenant_id', :t, true)"),
                    {"t": str(tenant)},
                )
                await conn.execute(text(statement))


async def test_tampering_is_detected(
    app_sessions: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine, tenant: uuid.UUID
) -> None:
    await _append(app_sessions, tenant, 3)
    # Simula um atacante com acesso de dono do esquema que desliga o gatilho.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant)}
        )
        await conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_immutable"))
        await conn.execute(
            text("""UPDATE audit_log SET payload = '{"i": 99}' WHERE tenant_id = :t AND seq = 2"""),
            {"t": str(tenant)},
        )
        await conn.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_immutable"))
    async with tenant_session(app_sessions, tenant) as session:
        assert await first_broken_seq(session, tenant) == 2


async def test_privacy_notice_versions_are_immutable(
    app_sessions: async_sessionmaker[AsyncSession], tenant: uuid.UUID
) -> None:
    async with tenant_session(app_sessions, tenant) as session:
        session.add(PrivacyNotice(tenant_id=tenant, version=1, content="v1", content_hash="0" * 64))
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with tenant_session(app_sessions, tenant) as session:
            await session.execute(text("UPDATE privacy_notice SET content = 'alterado'"))
