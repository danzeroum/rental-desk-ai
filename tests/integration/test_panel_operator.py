"""Painel do operador (RF23) e criação de imobiliária (tarefa global com papel de operação)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import CliError, provision_tenant
from app.core.config import Settings
from app.core.rls import global_session, tenant_session
from app.models import AppUser, AuditLog, PrivacyNotice, Tenant, TenantMember, TenantRoute
from app.onboarding import OnboardingError, create_tenant, slugify
from app.workers.queue import DEAD_LETTER
from tests.conftest import TEST_DATABASE_OPS_URL
from tests.integration.panel_client import PanelClient, panel_client, user_with_password

pytestmark = pytest.mark.integration
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
def panel(settings: Settings) -> Iterator[PanelClient]:
    with panel_client(settings) as client:
        yield client


async def _user(app: Sessions, *, operator: bool) -> tuple[uuid.UUID, str]:
    user_id, email, hashed = user_with_password("op" if operator else "g")
    async with global_session(app) as session:
        session.add(
            AppUser(
                id=user_id,
                email=email,
                display_name="Op",
                password_hash=hashed,
                is_operator=operator,
            )
        )
    return user_id, email


def test_slugify() -> None:
    assert slugify("  Imobiliária São João & Cia ") == "imobiliaria-sao-joao-cia"
    with pytest.raises(OnboardingError):
        slugify("!!!")


async def test_create_tenant_sets_everything_up(
    app_sessions: Sessions, ops_sessions: Sessions
) -> None:
    name = f"Imobiliária Teste {uuid.uuid4().hex[:6]}"
    first = await create_tenant(
        ops_sessions, app_sessions, name=name, gestor_email="G@Exemplo.com", gestor_name="Gil"
    )
    second = await create_tenant(
        ops_sessions, app_sessions, name=name, gestor_email="g@exemplo.com", gestor_name="Gil"
    )
    assert second.slug == f"{first.slug}-2" and second.gestor_id == first.gestor_id
    async with tenant_session(app_sessions, first.tenant_id) as session:
        tenant = await session.get(Tenant, first.tenant_id)
        route = await session.scalar(
            select(TenantRoute).where(
                TenantRoute.channel == "slug", TenantRoute.tenant_id == first.tenant_id
            )
        )
        notice = await session.scalar(select(PrivacyNotice))
        member = await session.scalar(select(TenantMember))
        event = await session.scalar(select(AuditLog.event_type))
    assert tenant is not None and tenant.status == "onboarding"
    assert route is not None and route.external_id == first.slug
    assert (
        notice is not None
        and notice.version == 1
        and name in notice.content
        and "PARAR" in notice.content
    )
    assert member is not None and member.role == "gestor" and event == "tenant_created"
    with pytest.raises(OnboardingError):
        await create_tenant(
            ops_sessions, app_sessions, name="X", gestor_email="sem-arroba", gestor_name=""
        )


async def test_only_operators_list_tenants(app_sessions: Sessions, ops_sessions: Sessions) -> None:
    await create_tenant(
        ops_sessions, app_sessions, name="Visível", gestor_email="v@exemplo.com", gestor_name="V"
    )
    operator, _ = await _user(app_sessions, operator=True)
    regular, _ = await _user(app_sessions, operator=False)
    async with global_session(app_sessions, user_id=regular) as session:
        assert (await session.execute(select(Tenant))).scalars().all() == []
    async with global_session(app_sessions) as session:
        assert (await session.execute(select(Tenant))).scalars().all() == []
    async with global_session(app_sessions, user_id=operator) as session:
        names = [t.name for t in (await session.execute(select(Tenant))).scalars()]
    assert "Visível" in names


async def test_operator_overview_and_support_access(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions
) -> None:
    created = await create_tenant(
        ops_sessions,
        app_sessions,
        name=f"Saúde {uuid.uuid4().hex[:6]}",
        gestor_email="s@exemplo.com",
        gestor_name="S",
    )
    _, email = await _user(app_sessions, operator=True)
    panel.run(
        panel.app.state.redis.xadd,
        DEAD_LETTER,
        {"kind": "conversation", "key": f"{created.tenant_id}:x"},
    )
    assert panel.login(email).headers["location"] == "/operador"
    page = panel.get("/operador")
    assert page.status_code == 200 and "1 tarefa(s) na fila de falhas" in page.text
    assert "sem número" in page.text and "Entrar como suporte" in page.text
    entered = panel.post("/painel/escolher", {"tenant_id": str(created.tenant_id)})
    assert entered.headers["location"] == "/painel"
    assert panel.get("/painel/configuracoes").status_code == 200  # suporte administra
    async with tenant_session(app_sessions, created.tenant_id) as session:
        events = list((await session.execute(select(AuditLog.event_type))).scalars())
    assert "support_access" in events


async def test_non_operator_is_refused(panel: PanelClient, app_sessions: Sessions) -> None:
    _, email = await _user(app_sessions, operator=False)
    panel.login(email)
    assert panel.get("/operador").status_code == 403


def test_cli_provision_tenant_invites_gestor(panel: PanelClient) -> None:
    service = panel.app.state.auth
    message = panel.run(
        provision_tenant,
        service,
        TEST_DATABASE_OPS_URL,
        "Imobiliária CLI",
        "cli@exemplo.com",
        "Cli",
    )
    assert "Imobiliária CLI criada" in message and "/convite/" in message
    again = panel.run(
        provision_tenant,
        service,
        TEST_DATABASE_OPS_URL,
        "Imobiliária CLI 2",
        "cli@exemplo.com",
        "Cli",
    )
    assert "/convite/" in again  # ainda sem senha: novo convite
    with pytest.raises(CliError):
        panel.run(provision_tenant, service, TEST_DATABASE_OPS_URL, "   ", "cli@exemplo.com", "Cli")
