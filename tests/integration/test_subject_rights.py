# ruff: noqa: F811 - as fixtures `world`, `redis` e `keyring` vêm de test_handoffs
"""Direitos do titular (RF21): PARAR/VOLTAR, exportar e apagar; aviso de privacidade público."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.rls import tenant_session
from app.domain import messages, notices
from app.models import AuditLog, Contact, Conversation, Message
from app.models.enums import Direction, MemberRole, MessageKind, MessageOrigin
from app.onboarding import create_tenant
from app.workers.processor import SkipReason
from tests.factories import create_tenant as bare_tenant
from tests.integration.panel_client import PanelClient, panel_client
from tests.integration.test_handoffs import (  # noqa: F401 - fixtures
    GUEST,
    World,
    keyring,
    redis,
    team,
    world,
)
from tests.integration.test_panel_manager import Team, _handoff, _member

pytestmark = pytest.mark.integration
Sessions = async_sessionmaker[AsyncSession]


async def _contact(world: World) -> Contact:
    async with tenant_session(world.app, world.tenant) as session:
        contact = await session.scalar(select(Contact).where(Contact.phone_e164 == f"+{GUEST}"))
    assert contact is not None
    return contact


async def test_stop_and_resume(world: World) -> None:
    await team(world)
    await world.say("Quero falar com um corretor")
    outcome = await world.say("PARAR")
    assert outcome.skipped == SkipReason.OPT_OUT_NOW
    assert world.gateway.texts[-1].body == messages.OPT_OUT_CONFIRMED
    assert (await _contact(world)).opted_out_at is not None
    sent = len(world.gateway.texts)
    assert (await world.say("oi?")).skipped == SkipReason.OPTED_OUT
    assert (await world.say("PARAR")).skipped == SkipReason.OPTED_OUT  # não confirma de novo
    world.advance(70)
    await world.service.escalate(world.tenant)  # não cai e não escreve ao hóspede
    assert not [m for m in world.gateway.texts[sent:] if m.to == GUEST]
    resumed = await world.say("voltar")
    assert resumed.skipped is None and world.gateway.texts[-1].to == GUEST
    assert (await _contact(world)).opted_out_at is None
    async with tenant_session(world.app, world.tenant) as session:
        events = set((await session.execute(select(AuditLog.event_type))).scalars())
    assert {"opt_out", "opt_out_confirmed", "opt_in_again"} <= events


async def test_stop_works_even_when_paused(world: World) -> None:
    await world.service.set_paused(world.tenant, paused=True, user_id=uuid.uuid4())
    await world.say("PARAR")
    assert (await _contact(world)).opted_out_at is not None


# --- Painel ------------------------------------------------------------------------------


@pytest.fixture
async def manager_team(app_sessions: Sessions, ops_sessions: Sessions) -> Team:
    tenant_id = await bare_tenant(ops_sessions, "Imobiliária Sol")
    gestor = await _member(app_sessions, tenant_id, MemberRole.GESTOR)
    corretor = await _member(app_sessions, tenant_id, MemberRole.CORRETOR)
    return Team(tenant_id, *gestor, *corretor)


@pytest.fixture
def panel(settings: Settings) -> Iterator[PanelClient]:
    with panel_client(settings) as client:
        yield client


async def _broker_mention(app: Sessions, tenant_id: uuid.UUID, phone: str) -> uuid.UUID:
    """Confirmação ao corretor (outra conversa) que cita o telefone do hóspede."""
    async with tenant_session(app, tenant_id) as session:
        conversation = await session.scalar(select(Conversation).limit(1))
        assert conversation is not None
        broker = Contact(tenant_id=tenant_id, phone_e164=f"+5511{uuid.uuid4().int % 10**9:09d}")
        session.add(broker)
        await session.flush()
        broker_conv = Conversation(
            tenant_id=tenant_id,
            contact_id=broker.id,
            privacy_notice_id=conversation.privacy_notice_id,
        )
        session.add(broker_conv)
        await session.flush()
        session.add(
            Message(
                tenant_id=tenant_id, conversation_id=broker_conv.id, direction=Direction.OUTBOUND,
                kind=MessageKind.TEXT, origin=MessageOrigin.ASSISTANT,
                body=notices.BROKER_ASSUMED.format(guest=phone),
            )
        )  # fmt: skip
        return broker_conv.id


async def test_panel_export_and_erase(
    panel: PanelClient, manager_team: Team, app_sessions: Sessions
) -> None:
    tenant = manager_team.tenant_id
    await _handoff(app_sessions, tenant)  # hóspede +5511977776666
    broker_conv = await _broker_mention(app_sessions, tenant, "+5511977776666")
    panel.login(manager_team.gestor_email)
    found = panel.post("/painel/titular", {"phone": "(11) 97777-6666", "action": "buscar"})
    assert (
        found.status_code == 200
        and "1 conversa(s)" in found.text
        and "passagem(ns) em andamento" in found.text
    )
    exported = panel.post("/painel/titular", {"phone": "11977776666", "action": "exportar"})
    assert exported.headers["content-type"].startswith("application/json")
    data = json.loads(exported.content)
    assert data["telefone"] == "+5511977776666" and data["conversas"][0]["passagens_ao_corretor"]
    refused = panel.post(
        "/painel/titular", {"phone": "11977776666", "action": "apagar", "confirm": "sim"}
    )
    assert refused.status_code == 400 and "Digite APAGAR" in refused.text
    erased = panel.post(
        "/painel/titular", {"phone": "11977776666", "action": "apagar", "confirm": "apagar"}
    )
    assert "Dados apagados." in erased.text
    async with tenant_session(app_sessions, tenant) as session:
        assert (
            await session.scalar(select(Contact).where(Contact.phone_e164 == "+5511977776666"))
            is None
        )
        body = await session.scalar(
            select(Message.body).where(Message.conversation_id == broker_conv)
        )
        payloads = (await session.execute(select(AuditLog.payload))).scalars().all()
        count = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == "subject_erased")
        )
    assert body is not None and "977776666" not in body and "[apagado a pedido]" in body
    assert count == 1 and not any("977776666" in json.dumps(p) for p in payloads)
    gone = panel.post("/painel/titular", {"phone": "11977776666", "action": "buscar"})
    assert "Nenhum dado com esse telefone" in gone.text
    bad = panel.post("/painel/titular", {"phone": "123", "action": "buscar"})
    assert bad.status_code == 400


async def test_corretor_cannot_use_subject_page(panel: PanelClient, manager_team: Team) -> None:
    panel.login(manager_team.corretor_email)
    assert panel.get("/painel/titular").status_code == 403


async def test_public_privacy_notice(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions
) -> None:
    created = await create_tenant(
        ops_sessions,
        app_sessions,
        name=f"Privada {uuid.uuid4().hex[:6]}",
        gestor_email="p@exemplo.com",
        gestor_name="P",
    )
    page = panel.get(f"/privacidade/{created.slug}")
    assert page.status_code == 200 and "controladora" in page.text and "Versão 1" in page.text
    assert panel.get(f"/privacidade/{created.slug}/v1").status_code == 200
    assert panel.get(f"/privacidade/{created.slug}/v9").status_code == 404
    assert panel.get("/privacidade/nao-existe").status_code == 404


async def test_member_stop_blocks_templates_and_commands(world: World) -> None:
    from tests.integration.test_handoffs import ANA  # noqa: PLC0415

    await team(world)
    await world.say("PARAR", sender=ANA.removeprefix("+"))  # corretora pede para sair
    assert world.gateway.texts[-1].body == messages.OPT_OUT_CONFIRMED
    sent = len(world.gateway.sent)
    assert (await world.say("1", sender=ANA.removeprefix("+"))).skipped == SkipReason.OPTED_OUT
    await world.say("Quero falar com um corretor")
    assert world.templates_to(ANA) == []  # nenhum template para quem saiu
    assert not [m for m in world.gateway.sent[sent:] if m.to == ANA.removeprefix("+")]
    async with tenant_session(world.app, world.tenant) as session:
        payload = await session.scalar(
            select(AuditLog.payload).where(AuditLog.event_type == "opt_out")
        )
    assert payload is not None and payload["command"] == "PARAR" and "at" in payload
