# ruff: noqa: F811 - as fixtures `redis` e `keyring` vêm de test_pipeline
"""Modo sombra (SPEC seção 11): registrar sem enviar, sem passagem real, conferir e ativar."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select, update

from app.adapters.fake_llm import FakeLLM
from app.adapters.mailer import FakeMailer
from app.core.config import Settings
from app.core.crypto import Keyring
from app.core.rls import tenant_session
from app.models import Handoff, Tenant
from app.models.enums import MemberRole
from app.workers.handoffs import HandoffService
from tests.integration.panel_client import PanelClient, panel_client
from tests.integration.test_panel_manager import _member
from tests.integration.test_pipeline import (  # noqa: F401 - fixtures
    NOW,
    Sessions,
    World,
    keyring,
    redis,
    setup_tenant,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def panel(settings: Settings) -> Iterator[PanelClient]:
    with panel_client(settings) as client:
        client.clock.now = NOW
        yield client


async def test_shadow_flow(
    panel: PanelClient,
    app_sessions: Sessions,
    ops_sessions: Sessions,
    redis: Redis,
    keyring: Keyring,
) -> None:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    async with tenant_session(app_sessions, tenant_id) as session:
        await session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(status="onboarding")
        )
    _, gestor = await _member(app_sessions, tenant_id, MemberRole.GESTOR)
    _, corretor = await _member(app_sessions, tenant_id, MemberRole.CORRETOR)
    panel.login(gestor)
    assert "Em implantação" in panel.get("/painel/sombra").text
    assert panel.post("/painel/situacao", {"target": "active"}).status_code == 400  # pula etapa
    assert panel.post("/painel/situacao", {"target": "shadow"}).status_code == 303

    world = World(app_sessions, redis, keyring, FakeLLM())
    world.pipeline.handoffs = HandoffService(
        sessions=app_sessions, gateway=world.gateway, keyring=keyring, mailer=FakeMailer(),
        clock=lambda: NOW,
    )  # fmt: skip
    await world.say(pnid, "Casa para 4 de 10/12 a 15/12")
    await world.say(pnid, "Quero falar com um corretor")
    assert world.gateway.sent == []  # nada vai ao hóspede nem ao corretor
    async with tenant_session(app_sessions, tenant_id) as session:
        assert await session.scalar(select(func.count()).select_from(Handoff)) == 0

    page = panel.get("/painel/sombra").text
    assert "Respostas registradas no modo sombra: 2" in page
    assert "Hóspede:</strong> Casa para 4 de 10/12 a 15/12" in page and "(CASA08)" in page
    assert "passaria ao corretor: pediu um corretor" in page
    early = panel.post("/painel/situacao", {"target": "active"})
    assert early.status_code == 400 and "2 dias" in early.text
    assert panel.post("/painel/situacao", {"target": "active", "early": "sim"}).status_code == 303
    async with tenant_session(app_sessions, tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
    assert tenant is not None and tenant.status == "active"
    panel.post("/sair")
    panel.login(corretor)
    assert panel.get("/painel/sombra").status_code == 403
