"""Isolamento entre imobiliárias (M1): RLS no banco + filtro na aplicação.

Aceite do SPEC: "um tenant não lê dados de outro, mesmo com consulta sem filtro".
As tabelas são descobertas a partir dos modelos, então uma tabela nova sem RLS
faz estes testes falharem.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.rls import TENANT_KEY, global_session, tenant_session
from app.models import GLOBAL_TABLES, Base, Contact, Property, Tenant
from tests.factories import create_tenant, create_user, seed_tenant_data

pytestmark = pytest.mark.integration

TENANT_TABLES = sorted(
    name for name, table in Base.metadata.tables.items() if "tenant_id" in table.columns
)
SHARED_READ_TABLES = {"tenant_route"}  # leitura global, escrita restrita (D011)
ISOLATED_READ_TABLES = [t for t in TENANT_TABLES if t not in SHARED_READ_TABLES]


@pytest.fixture
async def two_tenants(
    app_sessions: async_sessionmaker[AsyncSession], ops_sessions: async_sessionmaker[AsyncSession]
) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a = await create_tenant(ops_sessions, "Imobiliária A")
    tenant_b = await create_tenant(ops_sessions, "Imobiliária B")
    for tenant in (tenant_a, tenant_b):
        user = await create_user(app_sessions)
        await seed_tenant_data(app_sessions, tenant, user)
    return tenant_a, tenant_b


async def test_every_table_is_tenant_scoped_or_explicitly_global(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,"
                    " EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid"
                    "         AND a.attname = 'tenant_id' AND NOT a.attisdropped) AS has_tenant,"
                    " (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies"
                    " FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace"
                    " AND c.relkind = 'r'"
                )
            )
        ).all()
    problems = []
    for name, rls, forced, has_tenant, policies in rows:
        if name in GLOBAL_TABLES:
            continue
        if name != "tenant" and not has_tenant:
            problems.append(f"{name}: sem tenant_id e fora de GLOBAL_TABLES")
        if not (rls and forced and policies):
            problems.append(f"{name}: RLS={rls} FORCE={forced} políticas={policies}")
    assert problems == []


@pytest.mark.parametrize("table", ISOLATED_READ_TABLES)
async def test_unfiltered_query_sees_only_own_tenant(
    app_engine: AsyncEngine, two_tenants: tuple[uuid.UUID, uuid.UUID], table: str
) -> None:
    tenant_a, tenant_b = two_tenants
    async with app_engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant_a)}
        )
        tenants = (
            (
                await conn.execute(text(f"SELECT DISTINCT tenant_id FROM {table}"))  # noqa: S608
            )
            .scalars()
            .all()
        )
    assert tenants == [tenant_a]
    assert tenant_b not in tenants


@pytest.mark.parametrize("table", ISOLATED_READ_TABLES)
async def test_without_tenant_business_tables_are_empty(
    app_engine: AsyncEngine, two_tenants: tuple[uuid.UUID, uuid.UUID], table: str
) -> None:
    async with app_engine.connect() as conn:
        count = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
    assert count == 0


@pytest.mark.parametrize("table", ISOLATED_READ_TABLES)
async def test_cannot_update_or_delete_other_tenant_rows(
    app_engine: AsyncEngine, two_tenants: tuple[uuid.UUID, uuid.UUID], table: str
) -> None:
    if table in {"audit_log", "privacy_notice"}:
        pytest.skip("tabela somente inclusão: coberta em test_audit_log")
    tenant_a, tenant_b = two_tenants
    async with app_engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant_a)}
        )
        updated = await conn.execute(
            text(f"UPDATE {table} SET tenant_id = tenant_id WHERE tenant_id = :b"),  # noqa: S608
            {"b": str(tenant_b)},
        )
        deleted = await conn.execute(
            text(f"DELETE FROM {table} WHERE tenant_id = :b"),  # noqa: S608
            {"b": str(tenant_b)},
        )
        assert (updated.rowcount, deleted.rowcount) == (0, 0)


async def test_cannot_insert_row_for_other_tenant(
    app_sessions: async_sessionmaker[AsyncSession], two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, tenant_b = two_tenants
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with tenant_session(app_sessions, tenant_a) as session:
            session.add(Contact(tenant_id=tenant_b, phone_e164="+5511900000000"))


async def test_cannot_move_own_row_to_other_tenant(
    app_sessions: async_sessionmaker[AsyncSession], two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, tenant_b = two_tenants
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with tenant_session(app_sessions, tenant_a) as session:
            await session.execute(
                text("UPDATE contact SET tenant_id = :b WHERE tenant_id = :a"),
                {"a": str(tenant_a), "b": str(tenant_b)},
            )


async def test_tenant_table_shows_only_current_tenant(
    app_sessions: async_sessionmaker[AsyncSession], two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, _ = two_tenants
    async with tenant_session(app_sessions, tenant_a) as session:
        ids = (await session.execute(select(Tenant.id))).scalars().all()
    assert ids == [tenant_a]


async def test_app_role_cannot_create_tenants(
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    new_id = uuid.uuid4()
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with tenant_session(app_sessions, new_id) as session:
            session.add(Tenant(id=new_id, name="Intrusa", slug=f"x-{new_id.hex[:8]}"))


async def test_orm_filter_applies_even_with_explicit_other_tenant_where(
    app_sessions: async_sessionmaker[AsyncSession], two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, tenant_b = two_tenants
    async with tenant_session(app_sessions, tenant_a) as session:
        own = (await session.execute(select(Property))).scalars().all()
        other = (
            (await session.execute(select(Property).where(Property.tenant_id == tenant_b)))
            .scalars()
            .all()
        )
    assert {p.tenant_id for p in own} == {tenant_a}
    assert other == []


async def test_scope_survives_commit_inside_session(
    app_sessions: async_sessionmaker[AsyncSession], two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, _ = two_tenants
    async with app_sessions(info={TENANT_KEY: tenant_a}) as session:
        first = (await session.execute(text("SELECT DISTINCT tenant_id FROM contact"))).all()
        await session.commit()  # nova transação a seguir: o escopo precisa ser reaplicado
        second = (await session.execute(text("SELECT DISTINCT tenant_id FROM contact"))).all()
    assert first == second == [(tenant_a,)]


async def test_global_session_sees_no_business_rows(
    app_sessions: async_sessionmaker[AsyncSession], two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    async with global_session(app_sessions) as session:
        contacts = (await session.execute(text("SELECT count(*) FROM contact"))).scalar_one()
    assert contacts == 0


async def test_invalid_tenant_setting_fails_closed(app_engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="invalid input syntax for type uuid"):
        async with app_engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.current_tenant_id', 'x', true)"))
            await conn.execute(text("SELECT count(*) FROM contact"))


async def test_member_links_visible_to_logged_user_across_tenants(
    app_sessions: async_sessionmaker[AsyncSession], ops_sessions: async_sessionmaker[AsyncSession]
) -> None:
    user = await create_user(app_sessions)
    tenants = [await create_tenant(ops_sessions) for _ in range(2)]
    for tenant in tenants:
        async with tenant_session(app_sessions, tenant) as session:
            await session.execute(
                text(
                    "INSERT INTO tenant_member (tenant_id, user_id, role) VALUES (:t, :u, 'gestor')"
                ),
                {"t": str(tenant), "u": str(user)},
            )
    async with global_session(app_sessions, user_id=user) as session:
        visible = (
            (await session.execute(text("SELECT tenant_id FROM tenant_member"))).scalars().all()
        )
    assert sorted(visible) == sorted(tenants)


async def test_route_lookup_is_global_but_writes_are_scoped(
    app_sessions: async_sessionmaker[AsyncSession], two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, tenant_b = two_tenants
    async with global_session(app_sessions) as session:
        routed = (
            (await session.execute(text("SELECT DISTINCT tenant_id FROM tenant_route")))
            .scalars()
            .all()
        )
    assert {tenant_a, tenant_b} <= set(routed)
    async with tenant_session(app_sessions, tenant_a) as session:
        conn = await session.connection()
        result = await conn.execute(
            text("DELETE FROM tenant_route WHERE tenant_id = :b"), {"b": str(tenant_b)}
        )
        assert result.rowcount == 0
