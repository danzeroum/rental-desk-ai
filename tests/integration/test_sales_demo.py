# ruff: noqa: F811 - as fixtures `redis` e `keyring` vêm de test_pipeline
"""Kit de demonstração para vendas: link com token, conversa pelo pipeline real, aviso ao
corretor, "responda 1", revogação e limpeza diária."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.demo_gateway import DemoGateway, DemoOutbox, RoutingGateway, fill_template
from app.adapters.fake_llm import FakeLLM
from app.adapters.mailer import FakeMailer
from app.adapters.transcriber import FakeTranscriber
from app.adapters.whatsapp import Channel, FakeWhatsAppGateway
from app.core.config import Settings
from app.core.crypto import Keyring
from app.core.rls import global_session, tenant_session
from app.domain.render import LEGAL_NOTICE
from app.importer.sales_demo import SALES_DEMOS
from app.models import AppUser, AuditLog, Contact, Handoff, Property, Tenant, TenantMember
from app.models.enums import HandoffStatus
from app.sales_demo import purge_sales_demos, seed_sales_demos
from app.web.routes_demo import whatsapp_text
from app.workers.engine import ConversationEngine
from app.workers.handoffs import HandoffService
from app.workers.pipeline import ResponsePipeline
from app.workers.processor import ConversationProcessor
from app.workers.queue import WorkQueue
from tests.integration.panel_client import PanelClient, panel_client, user_with_password
from tests.integration.test_pipeline import Sessions, keyring, redis  # noqa: F401 - fixtures

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 23, 13, 0, tzinfo=UTC)
MARE, RECANTO = SALES_DEMOS
HX = {"hx-request": "true"}


@pytest.fixture
def panel(settings: Settings) -> Iterator[PanelClient]:
    with panel_client(settings) as client:
        yield client


@pytest.fixture
async def seeded(app_sessions: Sessions, ops_sessions: Sessions, keyring: Keyring) -> list[Any]:
    return await seed_sales_demos(ops_sessions, app_sessions, keyring, now=NOW)


class DemoWorker:
    """O worker de verdade, com o gateway de demonstração no lugar do WhatsApp."""

    def __init__(self, app: Sessions, redis: Redis, keyring: Keyring) -> None:
        self.app, self.queue = app, WorkQueue(redis)
        gateway = RoutingGateway(FakeWhatsAppGateway(), DemoGateway(DemoOutbox(redis)))
        handoffs = HandoffService(
            sessions=app, gateway=gateway, keyring=keyring, mailer=FakeMailer()
        )
        pipeline = ResponsePipeline(
            sessions=app, engine=ConversationEngine(FakeLLM()), gateway=gateway, keyring=keyring,
            public_base_url="https://app", handoffs=handoffs,
        )  # fmt: skip
        self.processor = ConversationProcessor(
            sessions=app, queue=self.queue, gateway=gateway, transcriber=FakeTranscriber(),
            keyring=keyring, handler=pipeline, members=handoffs,
        )  # fmt: skip

    async def drain(self) -> int:
        """Processa as conversas na fila, sem esperar o debounce."""
        members: Any = await self.queue.redis.zrange("rd:debounce:due", 0, -1)
        for member in members:
            tenant, conversation = str(member).split(":")
            await self.processor.process(uuid.UUID(tenant), uuid.UUID(conversation))
        return len(members)


async def _operator(app: Sessions) -> uuid.UUID:
    user_id, email, password_hash = user_with_password("op")
    async with global_session(app) as session:
        session.add(AppUser(id=user_id, email=email, display_name="Op", is_operator=True,
                            password_hash=password_hash))  # fmt: skip
    return user_id


def test_fill_template_and_bold() -> None:
    text = fill_template("passagem_corretor", ["Maré Alta", "escolheu uma casa", "+5500", "x"])
    assert text.startswith("Nova passagem da Maré Alta: escolheu uma casa. Hóspede: +5500.")
    assert "Responda 1" in text
    assert str(whatsapp_text("*Casa* <b>")) == "<strong>Casa</strong> &lt;b&gt;"


async def test_routing_gateway_keeps_real_numbers_real(redis: Redis) -> None:
    real = FakeWhatsAppGateway()
    gateway = RoutingGateway(real, DemoGateway(DemoOutbox(redis)))
    await gateway.send_text(Channel("demo-mare-alta", "x"), "5500911112222", "oi demo")
    await gateway.send_text(Channel("123456", "tok"), "5511999990000", "oi real")
    assert [m.body for m in real.sent] == ["oi real"]
    shown = await DemoOutbox(redis).read("demo-mare-alta", "+5500911112222")
    assert [e.text for e in shown] == ["oi demo"]


async def test_seed_creates_two_fictional_agencies(
    seeded: list[Any], app_sessions: Sessions, ops_sessions: Sessions, keyring: Keyring
) -> None:
    again = await seed_sales_demos(ops_sessions, app_sessions, keyring, now=NOW)
    assert [r.tenant_id for r in again] == [r.tenant_id for r in seeded]
    assert not any(r.created for r in again)
    for result, units in zip(seeded, (8, 6), strict=True):
        async with tenant_session(app_sessions, result.tenant_id) as session:
            tenant = await session.get(Tenant, result.tenant_id)
            assert tenant is not None and tenant.is_demo and tenant.status == "active"
            count = await session.scalar(
                select(func.count()).select_from(Property)
                .where(Property.snapshot_id == tenant.active_snapshot_id)
            )  # fmt: skip
            brokers = await session.scalar(
                select(func.count()).select_from(TenantMember)
                .where(TenantMember.whatsapp_phone.like("+5500%"))
            )  # fmt: skip
        assert count == units and brokers == 1
    assert RECANTO.unit_label == "quartos" and MARE.units == 8


async def test_visitor_conversation_handoff_and_assume(
    panel: PanelClient, seeded: list[Any], app_sessions: Sessions, redis: Redis,
    keyring: Keyring,
) -> None:  # fmt: skip
    service = panel.app.state.sales_demo
    token, link = panel.run(service.create_link, "visita teste", await _operator(app_sessions))
    worker = DemoWorker(app_sessions, redis, keyring)

    assert panel.get("/demo").status_code == 403  # sem link
    entered = panel.get(f"/demo/entrar/{token}")
    assert entered.status_code == 303 and entered.headers["location"] == "/demo"
    assert entered.headers["x-robots-tag"].startswith("noindex")
    choose = panel.get("/demo").text
    assert "Imobiliária Maré Alta" in choose and "6 quartos" in choose

    page = panel.get(f"/demo/{MARE.pnid}").text
    assert "dados fictícios" in page and "Responder 1 (assumir" not in page
    sent = panel.post(f"/demo/{MARE.pnid}/enviar", {"text": MARE.suggestions[0]}, headers=HX)
    assert sent.status_code == 200 and "digitando" in sent.text
    assert await worker.drain() == 1
    panes = panel.get(f"/demo/{MARE.pnid}/telas").text
    assert "assistente virtual da Imobiliária Maré Alta" in panes
    assert "<strong>" in panes and LEGAL_NOTICE in panes and "digitando" not in panes

    panel.post(f"/demo/{MARE.pnid}/enviar", {"text": "Quero a primeira"}, headers=HX)
    await worker.drain()
    panes = panel.get(f"/demo/{MARE.pnid}/telas").text
    assert "Nova passagem da Imobiliária Maré Alta" in panes and "Responder 1 (assumir" in panes

    assumed = panel.post(f"/demo/{MARE.pnid}/assumir", headers=HX)
    assert assumed.status_code == 200
    await worker.drain()
    panes = panel.get(f"/demo/{MARE.pnid}/telas").text
    assert "Você assumiu a conversa" in panes and "Responder 1 (assumir" not in panes
    assert "Um corretor assumiu" in panes
    tenant_id = seeded[0].tenant_id
    async with tenant_session(app_sessions, tenant_id) as session:
        handoff = await session.scalar(select(Handoff).order_by(Handoff.created_at.desc()))
        sends = await session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.event_type == "response_sent")
        )
    assert handoff is not None and handoff.status == HandoffStatus.ASSUMED
    assert handoff.assumed_via == "whatsapp" and sends

    # Recomeçar = hóspede novo; outra visita não vê esta conversa.
    panel.post(f"/demo/{MARE.pnid}/recomecar", headers=HX)
    assert "Nova passagem" not in panel.get(f"/demo/{MARE.pnid}/telas").text

    panel.run(service.revoke, link.id, link.created_by)
    assert panel.get(f"/demo/{MARE.pnid}").status_code == 403
    assert panel.get(f"/demo/entrar/{token}").status_code == 403


async def test_operator_creates_and_revokes_links(
    panel: PanelClient, app_sessions: Sessions
) -> None:
    user_id, email, password_hash = user_with_password("op")
    async with global_session(app_sessions) as session:
        session.add(AppUser(id=user_id, email=email, display_name="Op", is_operator=True,
                            password_hash=password_hash))  # fmt: skip
    panel.login(email)
    assert panel.post("/operador/demo", {"label": "  "}).status_code == 400
    created = panel.post("/operador/demo", {"label": "visita à Imobiliária X"})
    found = re.search(r"/demo/entrar/([A-Za-z0-9_\-]+)", created.text)
    assert created.status_code == 200 and found
    links = panel.run(panel.app.state.sales_demo.links)
    link = next(item for item in links if item.label == "visita à Imobiliária X")
    assert link.token_hash != found.group(1)  # só o hash fica no banco
    revoked = panel.post(f"/operador/demo/{link.id}/revogar")
    assert "Link revogado" in revoked.text
    assert panel.get(f"/demo/entrar/{found.group(1)}").status_code == 403


async def test_non_operator_cannot_manage_links(panel: PanelClient) -> None:
    assert panel.get("/operador/demo").status_code == 303  # sem sessão → login


async def test_daily_purge_removes_demo_visitors(
    seeded: list[Any], app_sessions: Sessions, redis: Redis, ops_engine: AsyncEngine,
) -> None:  # fmt: skip
    tenant_id = seeded[1].tenant_id
    async with tenant_session(app_sessions, tenant_id) as session:
        session.add(Contact(tenant_id=tenant_id, phone_e164="+5500912345678"))
    await DemoOutbox(redis).push(RECANTO.pnid, "5500912345678", "oi", inbound=True)
    early = datetime(2026, 11, 24, 4, 0, tzinfo=UTC)  # 1h em Brasília: ainda não
    assert await purge_sales_demos(redis, ops_engine, app_sessions, now=early) == 0
    night = datetime(2026, 11, 24, 7, 0, tzinfo=UTC)  # 4h em Brasília
    assert await purge_sales_demos(redis, ops_engine, app_sessions, now=night) >= 1
    assert await purge_sales_demos(redis, ops_engine, app_sessions, now=night) == 0  # 1x/dia
    async with tenant_session(app_sessions, tenant_id) as session:
        left = await session.scalar(select(func.count()).select_from(Contact))
    assert left == 0
    assert await DemoOutbox(redis).read(RECANTO.pnid, "5500912345678") == []
