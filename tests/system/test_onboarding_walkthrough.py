# ruff: noqa: F811 - as fixtures `redis` e `keyring` vêm de test_pipeline
"""Aceite do M10: uma imobiliária fictícia ativada do zero, seguindo só o painel (RF28)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.adapters.fake_llm import FakeLLM
from app.core.config import Settings
from app.core.crypto import Keyring
from app.core.rls import global_session, tenant_session
from app.core.security import hash_password
from app.importer.sample import build_sample
from app.importer.workbook import build_workbook
from app.models import AppUser, Tenant
from app.onboarding import create_tenant
from tests.integration.panel_client import PASSWORD, PanelClient, panel_client
from tests.integration.test_pipeline import Sessions, World, keyring, redis  # noqa: F401 - fixtures

pytestmark = pytest.mark.integration
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def panel(settings: Settings) -> Iterator[PanelClient]:
    with panel_client(settings) as client:
        yield client


def _progress(panel: PanelClient) -> str:
    page = panel.get("/painel/implantacao").text
    found = re.search(r"(\d) de 7 passos feitos", page)
    assert found, page
    return found.group(1)


async def test_new_agency_reaches_active_following_the_panel(
    panel: PanelClient,
    app_sessions: Sessions,
    ops_sessions: Sessions,
    redis: Redis,
    keyring: Keyring,
) -> None:
    email = f"gestor-{uuid.uuid4().hex[:8]}@exemplo.com"
    created = await create_tenant(
        ops_sessions,
        app_sessions,
        name=f"Praia {uuid.uuid4().hex[:4]}",
        gestor_email=email,
        gestor_name="Gi",
    )
    async with global_session(app_sessions) as session:
        user = await session.get(AppUser, created.gestor_id)
        assert user is not None
        user.password_hash = hash_password(PASSWORD)  # equivale a aceitar o convite
    panel.login(email)
    assert (
        _progress(panel) == "0"
        and "Agora: <strong>1. Conectar o WhatsApp" in panel.get("/painel/implantacao").text
    )

    pnid = str(uuid.uuid4().int % 10**15)
    assert (
        panel.post(
            "/painel/whatsapp",
            {"display_phone": "(13) 3333-0000", "phone_number_id": pnid, "access_token": "tok"},
        ).status_code
        == 303
    )
    assert _progress(panel) == "1"

    upload = panel.http.post(
        "/painel/dados", data={"csrf": panel.csrf()},
        files={"arquivo": ("casas.xlsx", build_workbook(build_sample(2026)), XLSX)},
        follow_redirects=False,
    )  # fmt: skip
    assert upload.status_code == 303 and _progress(panel) == "3"

    for text in ("Oi", "Casa para 4 de 10/12 a 15/12", "Tem piscina?"):
        assert panel.post("/painel/simulador", {"text": text}).status_code == 303
    assert "Casa para 4 de 10/12 a 15/12" in panel.get("/painel/simulador").text
    assert _progress(panel) == "4"

    assert panel.post(f"{upload.headers['location']}/aprovar").status_code == 303
    assert _progress(panel) == "5"
    assert panel.post("/painel/situacao", {"target": "shadow"}).status_code == 303
    assert _progress(panel) == "5"  # sombra começou, ainda não completou 2 dias
    shadow = panel.post("/painel/situacao", {"target": "active", "early": "sim"})
    assert (
        shadow.status_code == 400 and "Ainda não há respostas" in shadow.text
    )  # precisa de sombra real

    world = World(app_sessions, redis, keyring, FakeLLM())  # um hóspede escreve de verdade
    await world.say(pnid, "Casa para 4 de 10/12 a 15/12")
    assert world.gateway.sent == []  # sombra: nada sai
    assert "Hóspede:</strong> Casa para 4 de 10/12 a 15/12" in panel.get("/painel/sombra").text
    assert panel.post("/painel/situacao", {"target": "active", "early": "sim"}).status_code == 303
    assert _progress(panel) == "7" and "Concluída" in panel.get("/painel/implantacao").text
    async with tenant_session(app_sessions, created.tenant_id) as session:
        tenant = await session.get(Tenant, created.tenant_id)
    assert tenant is not None and tenant.status == "active"


async def test_number_of_other_agency_is_refused(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions
) -> None:
    pnid = str(uuid.uuid4().int % 10**15)
    emails = []
    for _ in range(2):
        email = f"g-{uuid.uuid4().hex[:8]}@exemplo.com"
        created = await create_tenant(
            ops_sessions,
            app_sessions,
            name=f"Mar {uuid.uuid4().hex[:4]}",
            gestor_email=email,
            gestor_name="G",
        )
        async with global_session(app_sessions) as session:
            user = await session.scalar(select(AppUser).where(AppUser.id == created.gestor_id))
            assert user is not None
            user.password_hash = hash_password(PASSWORD)
        emails.append(email)
    panel.login(emails[0])
    form = {"display_phone": "(13) 3333-0000", "phone_number_id": pnid, "access_token": "tok"}
    assert panel.post("/painel/whatsapp", form).status_code == 303
    panel.post("/sair")
    panel.login(emails[1])
    refused = panel.post("/painel/whatsapp", form)
    assert refused.status_code == 400 and "outra imobiliária" in refused.text
