"""Passagem ao corretor (M6): aviso, rodízio, escalonamento, assumir, desfecho e pausa."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.fake_llm import FakeLLM
from app.adapters.mailer import FakeMailer
from app.adapters.transcriber import FakeTranscriber
from app.adapters.whatsapp import FakeWhatsAppGateway
from app.adapters.whatsapp_webhook import EchoMessage, InboundMessage, WebhookBatch
from app.core.config import TIMEZONE, Settings
from app.core.crypto import Keyring
from app.core.rls import global_session, tenant_session
from app.domain import notices
from app.models import (
    AppUser,
    AuditLog,
    Consent,
    Contact,
    Conversation,
    Handoff,
    Tenant,
    TenantMember,
)
from app.models.enums import ConsentPurpose, HandoffOutcome, HandoffStatus, MemberRole, MessageKind
from app.workers.engine import ConversationEngine
from app.workers.handoffs import HandoffService, HandoffStateError
from app.workers.ingest import ingest_batch
from app.workers.pipeline import ResponsePipeline
from app.workers.processor import ConversationProcessor, SkipReason
from app.workers.queue import WorkQueue, conversation_key
from tests.integration.test_pipeline import setup_tenant

pytestmark = pytest.mark.integration
Sessions = async_sessionmaker[AsyncSession]
GUEST = "5511955554444"
# Segunda-feira, 23/11/2026, 10h em São Paulo (dentro do horário padrão 9h-18h).
MONDAY_10H = datetime(2026, 11, 23, 10, 0, tzinfo=TIMEZONE)


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def keyring(settings: Settings) -> Keyring:
    return Keyring.parse(settings.data_encryption_keys.get_secret_value())


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def add_member(
    app: Sessions, tenant_id: uuid.UUID, name: str, phone: str, role: MemberRole, *,
    consent: bool = True, operator: bool = False,
) -> uuid.UUID:  # fmt: skip
    user_id = uuid.uuid4()
    async with global_session(app) as session:
        session.add(
            AppUser(id=user_id, email=f"{name.lower()}-{user_id.hex[:6]}@x.com",
                    display_name=name, is_operator=operator)
        )  # fmt: skip
    async with tenant_session(app, tenant_id) as session:
        session.add(
            TenantMember(tenant_id=tenant_id, user_id=user_id, role=role, whatsapp_phone=phone)
        )
        if consent:
            session.add(
                Consent(tenant_id=tenant_id, user_id=user_id, phone_e164=phone,
                        purpose=ConsentPurpose.HANDOFF_ALERT, granted_at=MONDAY_10H,
                        evidence="aceite no painel (teste)")
            )  # fmt: skip
    return user_id


class World:
    def __init__(
        self, app: Sessions, redis: Redis, keyring: Keyring, pnid: str, tenant: uuid.UUID
    ) -> None:
        self.app, self.pnid, self.tenant = app, pnid, tenant
        self.clock = Clock(MONDAY_10H)
        self.gateway = FakeWhatsAppGateway()
        self.mailer = FakeMailer()
        self.queue = WorkQueue(redis)
        self.service = HandoffService(
            sessions=app,
            gateway=self.gateway,
            keyring=keyring,
            mailer=self.mailer,
            clock=self.clock,
        )
        pipeline = ResponsePipeline(
            sessions=app, engine=ConversationEngine(FakeLLM()), gateway=self.gateway,
            keyring=keyring, public_base_url="https://app", handoffs=self.service, clock=self.clock,
        )  # fmt: skip
        self.processor = ConversationProcessor(
            sessions=app, queue=self.queue, gateway=self.gateway, transcriber=FakeTranscriber(),
            keyring=keyring, handler=pipeline, members=self.service, clock=self.clock,
        )  # fmt: skip

    async def say(self, text: str, sender: str = GUEST) -> Any:
        msg = InboundMessage(
            phone_number_id=self.pnid, external_id=f"wamid.h{uuid.uuid4().hex}", wa_id=sender,
            kind=MessageKind.TEXT, timestamp=self.clock.now, text=text,
        )  # fmt: skip
        await ingest_batch(
            WebhookBatch(messages=[msg]), sessions=self.app, queue=self.queue, now=self.clock.now
        )
        async with tenant_session(self.app, self.tenant) as session:
            conversation_id = await session.scalar(
                select(Conversation.id)
                .join(Contact, Contact.id == Conversation.contact_id)
                .where(Contact.phone_e164 == f"+{sender}")
                .order_by(Conversation.created_at.desc())
                .limit(1)
            )
        assert conversation_id is not None
        return await self.processor.process(self.tenant, conversation_id)

    async def handoff(self) -> Handoff:
        async with tenant_session(self.app, self.tenant) as session:
            latest = (
                (await session.execute(select(Handoff).order_by(Handoff.created_at.desc())))
                .scalars()
                .first()
            )
        assert latest is not None
        return latest

    def advance(self, minutes: int) -> None:
        self.clock.now += timedelta(minutes=minutes)

    def templates_to(self, phone: str) -> list[str]:
        return [m.template or "" for m in self.gateway.templates if m.to == phone.removeprefix("+")]


@pytest.fixture
async def world(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> World:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    async with tenant_session(app_sessions, tenant_id) as session:
        await session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(
                contact_phone="+55 13 3333-0000",
                broker_hours={str(d): [["09:00", "18:00"]] for d in range(5)},  # seg-sex
                availability_updated_at=MONDAY_10H,
            )
        )  # fmt: skip
    return World(app_sessions, redis, keyring, pnid, tenant_id)


async def email_of(world: World, user_id: uuid.UUID) -> str:
    async with global_session(world.app) as session:
        user = await session.get(AppUser, user_id)
    assert user is not None
    return user.email


ANA, BIA, GESTORA = "+5511911110001", "+5511911110002", "+5511911110009"


async def team(world: World) -> dict[str, uuid.UUID]:
    return {
        "ana": await add_member(world.app, world.tenant, "Ana", ANA, MemberRole.CORRETOR),
        "bia": await add_member(world.app, world.tenant, "Bia", BIA, MemberRole.CORRETOR),
        "sem_consentimento": await add_member(
            world.app, world.tenant, "Caio", "+5511911110003", MemberRole.CORRETOR, consent=False
        ),
        "gestora": await add_member(world.app, world.tenant, "Gil", GESTORA, MemberRole.GESTOR),
        "operador": await add_member(
            world.app,
            world.tenant,
            "Op",
            "+5511911110010",
            MemberRole.GESTOR,
            consent=False,
            operator=True,
        ),
    }


async def test_handoff_in_hours_notifies_broker_and_copies_manager(world: World) -> None:
    ids = await team(world)
    await world.say("Quero falar com um corretor")
    handoff = await world.handoff()
    assert (handoff.reason, handoff.assigned_to, handoff.notify_count) == ("human", ids["ana"], 1)
    assert handoff.clock_started_at == MONDAY_10H
    assert world.templates_to(ANA) == ["passagem_corretor"]
    assert world.templates_to(GESTORA) == ["passagem_copia_gestor"]
    assert world.templates_to("+5511911110003") == []  # sem consentimento, sem aviso
    notice = next(m for m in world.gateway.templates if m.to == ANA.removeprefix("+"))
    assert notice.params[2] == f"+{GUEST}"
    guest_reply = world.gateway.texts[-1].body
    assert "Vou passar sua conversa para um corretor" in guest_reply


async def test_rotation_spreads_new_handoffs(world: World) -> None:
    ids = await team(world)
    await world.say("Quero falar com um corretor")
    await world.say("Quero falar com um corretor", sender="5511955550000")
    async with tenant_session(world.app, world.tenant) as session:
        assigned = (
            (await session.execute(select(Handoff.assigned_to).order_by(Handoff.created_at)))
            .scalars()
            .all()
        )
    assert assigned == [ids["ana"], ids["bia"]]


async def test_escalation_timeline_in_business_minutes(world: World) -> None:
    ids = await team(world)
    await world.say("Quero falar com um corretor")
    world.advance(9)
    assert await world.service.escalate(world.tenant) == 0
    world.advance(1)  # 10 min: próximo do rodízio
    assert await world.service.escalate(world.tenant) == 1
    assert (await world.handoff()).assigned_to == ids["bia"]
    world.advance(5)  # 15 min: alerta ao gestor e ao operador
    await world.service.escalate(world.tenant)
    assert "alerta_passagem" in world.templates_to(GESTORA)
    mine = [m for m in world.mailer.sent if m.to == await email_of(world, ids["operador"])]
    assert len(mine) == 1 and "15 min" in mine[0].subject
    world.advance(5)  # 20 min: volta para Ana
    await world.service.escalate(world.tenant)
    assert (await world.handoff()).assigned_to == ids["ana"]
    world.advance(40)  # 60 min: mensagem ao hóspede, uma vez
    await world.service.escalate(world.tenant)
    await world.service.escalate(world.tenant)
    waiting = [m.body for m in world.gateway.texts if "muitos atendimentos" in m.body]
    assert waiting == [notices.guest_waiting_text("+55 13 3333-0000")]
    mine = [m for m in world.mailer.sent if m.to == await email_of(world, ids["operador"])]
    assert len(mine) == 1  # alerta não se repete
    async with tenant_session(world.app, world.tenant) as session:
        events = (await session.execute(select(AuditLog.event_type))).scalars().all()
    assert {"handoff_notified", "handoff_alerted", "handoff_guest_waiting"} <= set(events)


async def test_out_of_hours_waits_for_next_shift(world: World) -> None:
    ids = await team(world)
    world.clock.now = datetime(2026, 11, 27, 20, 0, tzinfo=TIMEZONE)  # sexta 20h
    await world.say("Quero falar com um corretor")
    assert "voltam a atender segunda-feira a partir das 9h" in world.gateway.texts[-1].body
    handoff = await world.handoff()
    assert handoff.assigned_to is None
    assert handoff.clock_started_at == datetime(2026, 11, 30, 9, 0, tzinfo=TIMEZONE)
    assert world.gateway.templates == []
    world.clock.now = datetime(2026, 11, 28, 11, 0, tzinfo=TIMEZONE)  # sábado: fechado
    assert await world.service.escalate(world.tenant) == 0
    world.clock.now = datetime(2026, 11, 30, 9, 1, tzinfo=TIMEZONE)  # segunda, plantão aberto
    assert await world.service.escalate(world.tenant) == 1
    assert (await world.handoff()).assigned_to == ids["ana"]
    assert world.templates_to(ANA) == ["passagem_corretor"]


async def test_broker_replies_1_and_assistant_goes_silent(world: World) -> None:
    ids = await team(world)
    await world.say("Quero falar com um corretor")
    outcome = await world.say("1", sender=ANA.removeprefix("+"))
    assert outcome.skipped == SkipReason.MEMBER_COMMAND
    handoff = await world.handoff()
    assert (handoff.status, handoff.assumed_by, handoff.assumed_via) == (
        HandoffStatus.ASSUMED,
        ids["ana"],
        "whatsapp",
    )
    confirmation = world.gateway.texts[-1]
    assert confirmation.to == ANA.removeprefix("+")
    assert f"+{GUEST}" in confirmation.body
    async with tenant_session(world.app, world.tenant) as session:
        audited = await session.scalar(
            select(AuditLog.payload).where(AuditLog.event_type == "broker_reply")
        )
        conversation = await session.get(Conversation, handoff.conversation_id)
    assert audited is not None and GUEST not in str(audited) and "[TELEFONE]" in audited["text"]
    assert conversation is not None and conversation.muted_until == MONDAY_10H + timedelta(hours=24)
    sent_before = len(world.gateway.sent)
    muted = await world.say("Oi, alguém aí?")
    assert muted.skipped == SkipReason.MUTED
    assert len(world.gateway.sent) == sent_before
    world.advance(30)
    assert await world.service.escalate(world.tenant) == 0  # assumida: não escala


async def test_broker_message_without_pending_handoff(world: World) -> None:
    await team(world)
    outcome = await world.say("oi, tudo bem?", sender=BIA.removeprefix("+"))
    assert outcome.skipped == SkipReason.MEMBER_COMMAND
    assert world.gateway.texts[-1].body == notices.MEMBER_HELP
    await world.say("1", sender=BIA.removeprefix("+"))
    assert world.gateway.texts[-1].body == notices.BROKER_NOTHING_PENDING


async def test_panel_assume_outcome_and_double_assume(world: World) -> None:
    ids = await team(world)
    await world.say("Quero falar com um corretor")
    handoff = await world.handoff()
    await world.service.assume(world.tenant, handoff.id, ids["gestora"], via="panel")
    with pytest.raises(HandoffStateError, match="já foi assumida"):
        await world.service.assume(world.tenant, handoff.id, ids["bia"], via="panel")
    closed = await world.service.record_outcome(
        world.tenant, handoff.id, HandoffOutcome.WON, ids["gestora"]
    )
    assert (closed.status, closed.outcome) == (HandoffStatus.CLOSED, HandoffOutcome.WON)


async def test_pause_takes_effect_immediately(world: World) -> None:
    ids = await team(world)
    await world.service.set_paused(world.tenant, paused=True, user_id=ids["gestora"])
    outcome = await world.say("Casa para 4 de 10/12 a 15/12")
    assert outcome.skipped == SkipReason.TENANT_PAUSED
    assert world.gateway.sent == []
    await world.service.set_paused(world.tenant, paused=False, user_id=ids["gestora"])
    assert (await world.say("Casa para 4 de 10/12 a 15/12")).handled
    async with tenant_session(world.app, world.tenant) as session:
        events = (await session.execute(select(AuditLog.event_type))).scalars().all()
    assert "assistant_paused" in events and "assistant_resumed" in events


async def test_no_broker_on_duty_still_alerts_at_15_minutes(world: World) -> None:
    await add_member(world.app, world.tenant, "Gil", GESTORA, MemberRole.GESTOR)
    await world.say("Quero falar com um corretor")
    assert (await world.handoff()).assigned_to is None
    world.advance(15)
    await world.service.escalate(world.tenant)
    assert "alerta_passagem" in world.templates_to(GESTORA)


def test_key_helper() -> None:
    assert conversation_key(uuid.UUID(int=1), uuid.UUID(int=2)).count(":") == 1


@pytest.mark.parametrize("enabled", [False, True])
async def test_echo_from_business_app_mutes_only_when_enabled(world: World, enabled: bool) -> None:
    await team(world)
    await world.say("Quero falar com um corretor")
    echo = EchoMessage(
        phone_number_id=world.pnid, external_id=f"wamid.e{uuid.uuid4().hex}", to_wa_id=GUEST,
        kind=MessageKind.TEXT, timestamp=world.clock.now, text="Oi, aqui é a Ana",
    )  # fmt: skip
    await ingest_batch(
        WebhookBatch(echoes=[echo]), sessions=world.app, queue=world.queue,
        now=world.clock.now, echo_mute=enabled,
    )  # fmt: skip
    handoff = await world.handoff()
    async with tenant_session(world.app, world.tenant) as session:
        conversation = await session.get(Conversation, handoff.conversation_id)
    assert conversation is not None
    if enabled:
        assert (handoff.status, handoff.assumed_via) == (HandoffStatus.ASSUMED, "echo")
        assert conversation.muted_until == world.clock.now + timedelta(hours=24)
    else:
        assert handoff.status == HandoffStatus.OPEN
        assert conversation.muted_until is None


async def test_rotation_respects_duty_schedule(world: World) -> None:
    ids = await team(world)
    async with tenant_session(
        world.app, world.tenant
    ) as session:  # segunda: Ana à tarde, Bia de manhã
        await session.execute(
            update(TenantMember)
            .where(TenantMember.user_id == ids["ana"])
            .values(duty_hours={"0": [["13:00", "18:00"]]})
        )
        await session.execute(
            update(TenantMember).where(TenantMember.user_id == ids["bia"])
            .values(duty_hours={"0": [["09:00", "13:00"]]})
        )  # fmt: skip
    await world.say("Quero falar com um corretor")  # segunda, 10h
    assert world.templates_to(BIA) == ["passagem_corretor"] and world.templates_to(ANA) == []
    world.advance(10)
    await world.service.escalate(world.tenant)
    assert (await world.handoff()).assigned_to == ids["bia"]  # Ana não está de plantão: Bia de novo
