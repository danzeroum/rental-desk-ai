# ruff: noqa: F811 - as fixtures `redis` e `keyring` vêm de test_pipeline
"""Retenção diária: conversa parada há mais de 180 dias perde nome e telefone do hóspede."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.fake_llm import FakeLLM
from app.core.crypto import Keyring
from app.core.rls import tenant_session
from app.models import AuditLog, Contact, Conversation, Handoff, Message
from app.models.enums import HandoffStatus
from app.workers.retention import anonymize_tenant, run_retention
from tests.integration.test_pipeline import (  # noqa: F401 - fixtures
    GUEST,
    Sessions,
    World,
    keyring,
    redis,
    setup_tenant,
)

pytestmark = pytest.mark.integration

DAYS = 180


async def _guest_with_history(
    app: Sessions, ops: Sessions, redis: Redis, keyring: Keyring
) -> tuple[uuid.UUID, datetime]:
    """Hóspede com conversa, nome de perfil e o telefone citado no texto. Devolve a imobiliária
    e a hora da última mensagem."""
    tenant_id, pnid = await setup_tenant(app, ops, keyring)
    world = World(app, redis, keyring, FakeLLM())
    await world.say(pnid, f"Sou Maria Souza, meu zap é {GUEST}. Casa para 8 de 28/12 a 02/01")
    async with tenant_session(app, tenant_id) as session:
        await session.execute(update(Contact).values(profile_name="Maria Souza"))
        last = await session.scalar(select(func.max(Message.created_at)))
    assert last is not None
    return tenant_id, last


async def _contact(app: Sessions, tenant_id: uuid.UUID) -> Contact:
    async with tenant_session(app, tenant_id) as session:
        contact: Contact | None = await session.scalar(select(Contact))
    assert contact is not None
    return contact


async def test_anonymizes_after_the_period_and_keeps_the_trail(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    tenant_id, last = await _guest_with_history(app_sessions, ops_sessions, redis, keyring)

    early = await anonymize_tenant(
        app_sessions, tenant_id, now=last + timedelta(days=DAYS), days=DAYS
    )
    assert early.contacts == 0  # no dia exato do prazo, ainda não
    assert (await _contact(app_sessions, tenant_id)).phone_e164 == f"+{GUEST}"

    later = last + timedelta(days=DAYS, minutes=1)
    result = await anonymize_tenant(app_sessions, tenant_id, now=later, days=DAYS)
    assert result.contacts == 1 and result.messages >= 1

    contact = await _contact(app_sessions, tenant_id)
    assert contact.phone_e164 is None and contact.profile_name is None
    assert contact.anonymized_at == later
    async with tenant_session(app_sessions, tenant_id) as session:
        bodies = " ".join(b or "" for b in await session.scalars(select(Message.body)))
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.event_type == "retention_applied")
        )
    assert GUEST not in bodies and "Maria Souza" not in bodies
    assert "Casa para 8" in bodies  # o pedido continua legível, só sem quem pediu
    assert audit is not None and audit.payload["contacts"] == 1
    assert GUEST not in str(audit.payload)

    again = await anonymize_tenant(app_sessions, tenant_id, now=later, days=DAYS)
    assert again.contacts == 0  # já anonimizado: não repete


async def test_open_handoff_holds_the_contact(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    tenant_id, last = await _guest_with_history(app_sessions, ops_sessions, redis, keyring)
    async with tenant_session(app_sessions, tenant_id) as session:
        conversation_id = await session.scalar(select(Conversation.id))
        session.add(Handoff(
            tenant_id=tenant_id, conversation_id=conversation_id, reason="reserva",
            summary={}, status=HandoffStatus.ASSUMED.value, assumed_at=last,
            assumed_via="panel",
        ))  # fmt: skip

    result = await anonymize_tenant(
        app_sessions, tenant_id, now=last + timedelta(days=DAYS + 30), days=DAYS
    )
    assert result.contacts == 0
    assert (await _contact(app_sessions, tenant_id)).phone_e164 == f"+{GUEST}"


async def test_opted_out_guest_keeps_only_the_phone(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    """Sem o número, quem pediu PARAR voltaria a receber atendimento automático."""
    tenant_id, last = await _guest_with_history(app_sessions, ops_sessions, redis, keyring)
    async with tenant_session(app_sessions, tenant_id) as session:
        await session.execute(update(Contact).values(opted_out_at=last))

    await anonymize_tenant(app_sessions, tenant_id, now=last + timedelta(days=DAYS + 1), days=DAYS)
    contact = await _contact(app_sessions, tenant_id)
    assert contact.phone_e164 == f"+{GUEST}" and contact.profile_name is None
    assert contact.anonymized_at is not None


async def test_runs_once_a_day_after_four_am(
    app_sessions: Sessions, ops_engine: AsyncEngine, redis: Redis
) -> None:
    night = datetime(2026, 11, 24, 5, 0, tzinfo=UTC)  # 2h em Brasília: cedo demais
    assert await run_retention(redis, ops_engine, app_sessions, now=night, days=DAYS) == 0
    assert not await redis.exists("rd:retention:2026-11-24")
    morning = datetime(2026, 11, 24, 7, 30, tzinfo=UTC)  # 4h30 em Brasília
    await run_retention(redis, ops_engine, app_sessions, now=morning, days=DAYS)
    assert await redis.exists("rd:retention:2026-11-24")
    assert await run_retention(redis, ops_engine, app_sessions, now=morning, days=DAYS) == 0
