# ruff: noqa: F811 - as fixtures `world`, `redis` e `keyring` vêm de test_handoffs
"""Lembrete das 9h (RF16): envio com consentimento, "OK" renova, "CASA 28/12-02/01" bloqueia."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import TIMEZONE
from app.core.rls import tenant_session
from app.domain import notices
from app.models import AuditLog, Block, Consent, Property, Tenant
from app.models.enums import BlockSource, ConsentPurpose, MemberRole
from app.scheduler.app import send_daily_reminders
from app.workers.reminders import MemberCommands, ReminderService, reminder_due
from tests.integration.test_handoffs import (  # noqa: F401 - fixtures
    MONDAY_10H,
    Sessions,
    World,
    add_member,
    keyring,
    redis,
    world,
)

pytestmark = pytest.mark.integration
GESTOR = "+5513999990001"
CORRETOR = "+5513999990002"


def reminders_for(world: World) -> ReminderService:
    return ReminderService(
        sessions=world.app, gateway=world.gateway, keyring=world.processor.keyring,
        public_base_url="https://painel.exemplo", clock=world.clock,
    )  # fmt: skip


async def _consent(app: Sessions, tenant: uuid.UUID, user: uuid.UUID, phone: str) -> None:
    async with tenant_session(app, tenant) as session:
        session.add(
            Consent(tenant_id=tenant, user_id=user, phone_e164=phone,
                    purpose=ConsentPurpose.DAILY_REMINDER, granted_at=MONDAY_10H,
                    evidence="aceite no painel (teste)")
        )  # fmt: skip


async def _code(world: World) -> str:
    async with tenant_session(world.app, world.tenant) as session:
        tenant = await session.get(Tenant, world.tenant)
        assert tenant is not None
        code = await session.scalar(
            select(Property.code).where(Property.snapshot_id == tenant.active_snapshot_id).limit(1)
        )
    assert code
    return code


def test_due_window() -> None:
    day = date(2026, 11, 23)
    assert not reminder_due(datetime(day.year, day.month, day.day, 8, 59, tzinfo=TIMEZONE))
    assert reminder_due(datetime(day.year, day.month, day.day, 9, 0, tzinfo=TIMEZONE))
    assert not reminder_due(datetime(day.year, day.month, day.day, 18, 0, tzinfo=TIMEZONE))


async def test_reminder_only_to_consenting_managers(world: World) -> None:
    gestor = await add_member(world.app, world.tenant, "Gil", GESTOR, MemberRole.GESTOR)
    await add_member(world.app, world.tenant, "Bia", CORRETOR, MemberRole.CORRETOR)
    other = await add_member(world.app, world.tenant, "Ana", "+5513999990003", MemberRole.GESTOR)
    await _consent(world.app, world.tenant, gestor, GESTOR)
    await _consent(world.app, world.tenant, other, "+5513000000000")  # telefone trocado
    assert await reminders_for(world).send(world.tenant) == 1
    [sent] = world.gateway.templates
    assert sent.template == notices.DAILY_REMINDER.name and sent.to == GESTOR.removeprefix("+")
    assert sent.params[0] == "Imobiliária Demo Riviera" and "bloqueio" in sent.params[1]
    assert sent.params[2] == "https://painel.exemplo/painel/bloqueios"


async def test_ok_and_block_replies(world: World) -> None:
    world.processor.members = MemberCommands(reminders_for(world), world.service)
    await add_member(world.app, world.tenant, "Gil", GESTOR, MemberRole.GESTOR)
    code = await _code(world)
    async with tenant_session(world.app, world.tenant) as session:
        await session.execute(
            update(Tenant).where(Tenant.id == world.tenant).values(availability_updated_at=None)
        )
    await world.say(f"{code.lower()} 28/12-02/01", sender=GESTOR.removeprefix("+"))
    answer = world.gateway.texts[-1].body
    assert "Bloqueado" in answer and "28/12/2026 a 02/01/2027" in answer and "responda OK" in answer
    await world.say("ok", sender=GESTOR.removeprefix("+"))
    assert world.gateway.texts[-1].body == notices.REMINDER_CONFIRMED
    await world.say("CASA999 28/12-02/01", sender=GESTOR.removeprefix("+"))
    assert "não encontrada" in world.gateway.texts[-1].body
    await world.say("1", sender=GESTOR.removeprefix("+"))  # cai na passagem
    assert world.gateway.texts[-1].body == notices.BROKER_NOTHING_PENDING
    async with tenant_session(world.app, world.tenant) as session:
        block = await session.scalar(select(Block).where(Block.source == BlockSource.REMINDER))
        tenant = await session.get(Tenant, world.tenant)
        events = list((await session.execute(select(AuditLog.event_type))).scalars())
    assert block is not None and block.property_code == code
    assert (block.start_date, block.end_date) == (date(2026, 12, 28), date(2027, 1, 2))
    assert tenant is not None and tenant.availability_updated_at == MONDAY_10H
    assert {"block_added", "availability_confirmed", "reminder_reply"} <= set(events)


async def test_guest_text_like_ok_is_not_a_command(world: World) -> None:
    world.processor.members = MemberCommands(reminders_for(world), world.service)
    sent_before = len(world.gateway.texts)
    await world.say("ok")  # hóspede: segue o fluxo normal do assistente
    assert len(world.gateway.texts) == sent_before + 1
    assert world.gateway.texts[-1].body != notices.REMINDER_CONFIRMED


async def test_scheduler_sends_once_per_day_in_window(
    world: World,
    redis: Redis,
    ops_engine: AsyncEngine,
    ops_sessions: Sessions,
) -> None:
    phone = (
        f"+5513{uuid.uuid4().int % 10**9:09d}"  # único: o banco de teste guarda execuções antigas
    )
    gestor = await add_member(world.app, world.tenant, "Gil", phone, MemberRole.GESTOR)
    await _consent(world.app, world.tenant, gestor, phone)
    service = reminders_for(world)

    def to_gestor() -> int:
        return sum(m.to == phone.removeprefix("+") for m in world.gateway.templates)

    world.clock.now = datetime(2026, 11, 23, 8, 0, tzinfo=TIMEZONE)
    await send_daily_reminders(redis, ops_engine, service)
    assert to_gestor() == 0
    world.clock.now = datetime(2026, 11, 23, 9, 5, tzinfo=TIMEZONE)
    await send_daily_reminders(redis, ops_engine, service)
    await send_daily_reminders(redis, ops_engine, service)  # já foi hoje
    assert to_gestor() == 1
    world.clock.now += timedelta(days=1)
    await send_daily_reminders(redis, ops_engine, service)
    assert to_gestor() == 2
    async with ops_sessions() as session, session.begin():  # só o papel de operação marca demo
        await session.execute(update(Tenant).where(Tenant.id == world.tenant).values(is_demo=True))
    world.clock.now += timedelta(days=1)
    await send_daily_reminders(redis, ops_engine, service)
    assert to_gestor() == 2  # demonstração não recebe
