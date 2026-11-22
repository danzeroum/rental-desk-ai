# ruff: noqa: F811 - as fixtures `redis` e `keyring` vêm de test_pipeline
"""Painel de resultados (RF30) e escala de plantão (RF29) no painel."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.adapters.fake_llm import FakeLLM
from app.core.config import Settings
from app.core.crypto import Keyring
from app.core.rls import tenant_session
from app.models import TenantMember
from app.models.enums import MemberRole
from tests.integration.panel_client import PanelClient, panel_client
from tests.integration.test_panel_manager import _member
from tests.integration.test_pipeline import Sessions, World, keyring, redis, setup_tenant  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def panel(settings: Settings) -> Iterator[PanelClient]:
    with panel_client(settings) as client:
        client.clock.now = datetime.now(UTC)  # o banco carimba a hora real nas conversas
        yield client


async def test_results_and_duty(
    panel: PanelClient,
    app_sessions: Sessions,
    ops_sessions: Sessions,
    redis: Redis,
    keyring: Keyring,
) -> None:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    world = World(app_sessions, redis, keyring, FakeLLM())
    world.pipeline.clock = world.processor.clock = lambda: datetime.now(UTC)
    await world.say(pnid, "Casa para 4 de 10/12 a 15/12")
    _, gestor = await _member(app_sessions, tenant_id, MemberRole.GESTOR)
    corretor_id, corretor = await _member(app_sessions, tenant_id, MemberRole.CORRETOR)
    panel.login(gestor)
    page = panel.get("/painel/resultados?periodo=7d").text
    assert (
        "Contatos atendidos" in page and '<path class="barra"' in page and "Ver em tabela" in page
    )
    assert ">1</p>" in page  # um contato
    assert "Contatos por dia" not in panel.get("/painel/resultados?periodo=hoje").text
    assert panel.get("/painel/resultados?periodo=xyz").status_code == 200  # volta ao padrão

    assert (
        "Seguir o horário da imobiliária"
        in panel.get(f"/painel/membros/{corretor_id}/plantao").text
    )
    bad = panel.post(f"/painel/membros/{corretor_id}/plantao", {"d0_inicio": "10:00"})
    assert bad.status_code == 400
    ok = panel.post(
        f"/painel/membros/{corretor_id}/plantao", {"d0_inicio": "09:00", "d0_fim": "13:00"}
    )
    assert ok.status_code == 303
    async with tenant_session(app_sessions, tenant_id) as session:
        member = await session.scalar(
            select(TenantMember).where(TenantMember.user_id == corretor_id)
        )
    assert member is not None and member.duty_hours == {"0": [["09:00", "13:00"]]}
    panel.post(f"/painel/membros/{corretor_id}/plantao", {"follows_tenant": "sim"})
    async with tenant_session(app_sessions, tenant_id) as session:
        member = await session.scalar(
            select(TenantMember).where(TenantMember.user_id == corretor_id)
        )
    assert member is not None and member.duty_hours is None
    panel.post("/sair")
    panel.login(corretor)
    assert panel.get("/painel/resultados").status_code == 403
