# ruff: noqa: F811 - as fixtures `redis` e `keyring` vêm de test_pipeline
"""Rastreio do operador: trilha por imobiliária, cadeia de hash conferida, CSV e linha do tempo."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.adapters.fake_llm import FakeLLM
from app.adapters.whatsapp_webhook import StatusUpdate, WebhookBatch
from app.core.config import Settings
from app.core.crypto import Keyring
from app.core.rls import global_session, tenant_session
from app.models import AppUser, AuditLog
from app.web.routes_trace import csv_line, mask_phone
from app.workers.ingest import ingest_batch
from tests.integration.panel_client import PanelClient, panel_client, user_with_password
from tests.integration.test_pipeline import (  # noqa: F401 - fixtures
    GUEST,
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
    with panel_client(settings, flush=False) as client:
        yield client


async def _login_operator(panel: PanelClient, app: Sessions) -> None:
    user_id, email, password_hash = user_with_password("op")
    async with global_session(app) as session:
        session.add(AppUser(id=user_id, email=email, display_name="Op", is_operator=True,
                            password_hash=password_hash))  # fmt: skip
    panel.login(email)


def test_csv_neutralizes_formulas_and_quotes() -> None:
    line = csv_line(['=HYPERLINK("x")', "+1", "normal", 7, None])
    assert line == '"\'=HYPERLINK(""x"")","\'+1","normal","7",""\r\n'
    assert next(csv.reader(io.StringIO(line)))[0] == '\'=HYPERLINK("x")'


def test_mask_phone() -> None:
    assert mask_phone("+5511996685998") == "+55 11 9****-5998"
    assert mask_phone("123") == "****"


async def test_trace_shows_chain_delivery_and_conversation(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions, redis: Redis,
    keyring: Keyring,
) -> None:  # fmt: skip
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    world = World(app_sessions, redis, keyring, FakeLLM())
    await world.say(pnid, "Casa para 8 pessoas de 28/12 a 02/01")
    wamid = world.gateway.sent[-1].external_id
    await ingest_batch(
        WebhookBatch(statuses=[StatusUpdate(pnid, wamid, "failed", NOW, 130497, "restricted")]),
        sessions=app_sessions, queue=world.queue, now=NOW,
    )  # fmt: skip
    await _login_operator(panel, app_sessions)
    panel.clock.now = NOW.replace(hour=16)

    search = {"imobiliaria": str(tenant_id), "periodo": "7d", "motivo": "comprovacao"}
    no_reason = panel.post("/operador/rastreio", {"imobiliaria": str(tenant_id), "periodo": "7d"})
    assert "motivo da consulta" in no_reason.text and "<tbody>" not in no_reason.text
    page = panel.post("/operador/rastreio", {**search, "telefone": GUEST})
    assert page.status_code == 200, page.text
    assert "cadeia íntegra" in page.text
    assert (
        "Mensagem recebida do hóspede" in page.text
        and "NÃO ENTREGUE: código 130497: a Meta bloqueou" in page.text
    )
    assert GUEST not in page.text.split("<tbody>")[1]  # nos resultados, nunca o número completo

    again = panel.get(f"/operador/rastreio?imobiliaria={tenant_id}&periodo=7d&motivo=chamado")
    assert "Rastreio consultado" not in again.text  # acessos fora da visão "todos"
    accesses = panel.get(
        f"/operador/rastreio?imobiliaria={tenant_id}&periodo=7d&tipo=acessos&motivo=auditoria"
    )
    assert "Comprovação pedida pelo cliente" in accesses.text  # o motivo aparece na trilha
    assert "Rastreio consultado" in accesses.text
    assert "Mensagem recebida do hóspede" not in accesses.text

    other = panel.post("/operador/rastreio", {**search, "telefone": "11900000000"})
    assert "Nenhum evento nesse filtro" in other.text

    exported = panel.post("/operador/rastreio/exportar", search)
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert {"message_received", "response_sent", "delivery_status", "trace_viewed"} <= {
        r["evento"] for r in rows
    }  # o CSV de "todos" é a prova completa, com os acessos
    assert all(len(r["hash"]) == 64 for r in rows)

    async with tenant_session(app_sessions, tenant_id) as session:
        conversation_id = await session.scalar(
            select(AuditLog.conversation_id).where(AuditLog.event_type == "message_received")
        )
        viewed = await session.scalar(
            select(AuditLog).where(AuditLog.event_type == "trace_exported")
        )
    assert viewed is not None and viewed.payload["rows"] >= 3

    base = f"/operador/rastreio/conversa/{conversation_id}?imobiliaria={tenant_id}"
    assert panel.get(base).status_code == 303  # sem motivo, volta para o rastreio
    timeline = panel.get(base + "&motivo=comprovacao")
    assert timeline.status_code == 200
    assert "Casa para 8 pessoas" in timeline.text and "NÃO ENTREGUE" in timeline.text
    assert mask_phone("+" + GUEST) in timeline.text


async def test_phone_never_travels_in_the_url(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions, keyring: Keyring,
) -> None:  # fmt: skip
    """Telefone na URL vai para o log de acesso (revisão LGPD, Risco-002): por GET é ignorado."""
    tenant_id, _ = await setup_tenant(app_sessions, ops_sessions, keyring)
    await _login_operator(panel, app_sessions)
    url = f"/operador/rastreio?imobiliaria={tenant_id}&telefone=11900000000"
    page = panel.get(url)
    assert page.status_code == 200
    assert 'value="11900000000"' not in page.text  # o filtro da URL não foi aplicado
    assert 'method="post" action="/operador/rastreio"' in page.text
    assert panel.get(f"/operador/rastreio/exportar?imobiliaria={tenant_id}").status_code == 405
    no_csrf = panel.http.post("/operador/rastreio", data={"imobiliaria": str(tenant_id)})
    assert no_csrf.status_code == 403


async def test_trace_requires_operator(panel: PanelClient) -> None:
    assert panel.get("/operador/rastreio").status_code == 303  # sem sessão → login
