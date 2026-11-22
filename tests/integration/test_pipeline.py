"""Pipeline de resposta (M5) com banco real: contexto → motor → envio → gravação."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.fake_llm import FakeLLM
from app.adapters.link_checker import FakeLinkChecker
from app.adapters.llm import LLMResponse
from app.adapters.transcriber import FakeTranscriber
from app.adapters.whatsapp import FakeWhatsAppGateway, GatewayError
from app.adapters.whatsapp_webhook import InboundMessage
from app.adapters.whatsapp_webhook import WebhookBatch as Batch
from app.core.audit import first_broken_seq
from app.core.config import Settings
from app.core.crypto import Keyring
from app.core.rls import tenant_session
from app.domain.render import CONTINGENCY, LEGAL_NOTICE
from app.importer.sample import build_sample
from app.importer.service import approve_snapshot, prepare_import, save_draft
from app.importer.workbook import build_workbook
from app.models import (
    AuditLog,
    Conversation,
    Handoff,
    Intent,
    Message,
    PrivacyNotice,
    SearchResult,
    Tenant,
    TenantRoute,
    WhatsAppAccount,
)
from app.models.enums import MessageKind, TenantStatus
from app.workers.channel import seal_token
from app.workers.engine import ConversationEngine
from app.workers.ingest import ingest_batch
from app.workers.pipeline import ResponsePipeline
from app.workers.processor import ConversationProcessor, SkipReason
from app.workers.queue import WorkQueue
from tests.factories import create_tenant, create_user
from tests.golden.runner import RecordingLLM

pytestmark = pytest.mark.integration

Sessions = async_sessionmaker[AsyncSession]
GUEST = "5511977776666"
NOW = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)


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


async def setup_tenant(
    app: Sessions, ops: Sessions, keyring: Keyring, status: TenantStatus = TenantStatus.ACTIVE
) -> tuple[uuid.UUID, str]:
    tenant_id = await create_tenant(ops, "Imobiliária Demo Riviera", status=status)
    user = await create_user(app)
    pnid = f"PN{uuid.uuid4().hex[:10]}"
    report = await prepare_import(
        build_workbook(build_sample(2026)), today=date(2026, 11, 21), link_checker=FakeLinkChecker()
    )
    async with tenant_session(app, tenant_id, user_id=user) as session:
        session.add(TenantRoute(channel="whatsapp", external_id=pnid, tenant_id=tenant_id))
        session.add(
            WhatsAppAccount(
                tenant_id=tenant_id, phone_number_id=pnid, display_phone="+551130000000",
                access_token_enc=seal_token(keyring, tenant_id, "token"),
            )
        )  # fmt: skip
        session.add(
            PrivacyNotice(tenant_id=tenant_id, version=1, content="Aviso", content_hash="0" * 64)
        )
        snapshot = await save_draft(
            session, tenant_id=tenant_id, user_id=user, report=report, file_name="demo.xlsx"
        )
        await approve_snapshot(
            session, tenant_id=tenant_id, snapshot_id=snapshot.id, user_id=user, now=NOW
        )
    return tenant_id, pnid


class World:
    def __init__(self, app: Sessions, redis: Redis, keyring: Keyring, llm: Any) -> None:
        self.app, self.keyring = app, keyring
        self.gateway = FakeWhatsAppGateway()
        self.llm = RecordingLLM(llm)
        self.queue = WorkQueue(redis)
        self.pipeline = ResponsePipeline(
            sessions=app, engine=ConversationEngine(self.llm), gateway=self.gateway,
            keyring=keyring, public_base_url="https://app.exemplo.com.br", clock=lambda: NOW,
        )  # fmt: skip
        self.processor = ConversationProcessor(
            sessions=app, queue=self.queue, gateway=self.gateway, transcriber=FakeTranscriber(),
            keyring=keyring, handler=self.pipeline, clock=lambda: NOW,
        )  # fmt: skip
        self.counter = 0

    async def say(self, pnid: str, text: str) -> Any:
        self.counter += 1
        msg = InboundMessage(
            phone_number_id=pnid, external_id=f"wamid.p{uuid.uuid4().hex}", wa_id=GUEST,
            kind=MessageKind.TEXT, timestamp=NOW, text=text, profile_name="Ana Souza",
        )  # fmt: skip
        await ingest_batch(Batch(messages=[msg]), sessions=self.app, queue=self.queue, now=NOW)
        tenant_id, conversation_id = await self.conversation()
        return await self.processor.process(tenant_id, conversation_id)

    async def conversation(self) -> tuple[uuid.UUID, uuid.UUID]:
        members: Any = await self.queue.redis.zrange("rd:debounce:due", 0, 0)
        tenant, conversation = str(members[0]).split(":")
        return uuid.UUID(tenant), uuid.UUID(conversation)


async def count(app: Sessions, tenant: uuid.UUID, model: Any) -> int:
    async with tenant_session(app, tenant) as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_kickoff_request_end_to_end(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    world = World(app_sessions, redis, keyring, FakeLLM())
    outcome = await world.say(pnid, "Casa para 12 no Réveillon com piscina")
    assert outcome.handled
    (sent,) = world.gateway.sent
    assert sent.to == GUEST
    assert "assistente virtual da Imobiliária Demo Riviera" in sent.body
    assert "https://app.exemplo.com.br/privacidade/" in sent.body and "/v1" in sent.body
    assert "(CASA06)" in sent.body and "com o pacote Réveillon" in sent.body
    assert LEGAL_NOTICE in sent.body
    # Nada de nome ou telefone do hóspede no LLM (SPEC, seção 7, item 8).
    assert all(GUEST not in p and "Ana" not in p for p in world.llm.prompts)
    async with tenant_session(app_sessions, tenant_id) as session:
        conversation = (await session.execute(select(Conversation))).scalar_one()
        audit = (
            await session.execute(select(AuditLog).where(AuditLog.event_type == "response_sent"))
        ).scalar_one()
        unprocessed = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.direction == "in", Message.processed_at.is_(None))
        )
        assert await first_broken_seq(session, tenant_id) is None
    assert conversation.state_json["last_offered"] == ["CASA06", "CASA01", "CASA10"]
    assert conversation.first_response_at is not None
    assert audit.snapshot_hash is not None and len(audit.snapshot_hash) == 64
    assert audit.payload["extraction"]["intent"] == "search"
    assert [r["code"] for r in audit.payload["search"]["results"]] == ["CASA06", "CASA01", "CASA10"]
    assert audit.payload["text"] == sent.body
    assert unprocessed == 0
    assert await count(app_sessions, tenant_id, Intent) == 1
    assert await count(app_sessions, tenant_id, SearchResult) == 1


async def test_follow_up_uses_saved_state_without_new_intro(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    _, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    world = World(app_sessions, redis, keyring, FakeLLM())
    await world.say(pnid, "Quero uma casa para 6 pessoas de 10/12 a 15/12")
    await world.say(pnid, "A segunda tem churrasqueira?")
    second = world.gateway.sent[1].body
    assert second == "A Sobrado Azul (CASA03): churrasqueira, sim."


async def test_handoff_is_recorded_with_summary(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    world = World(app_sessions, redis, keyring, FakeLLM())
    await world.say(pnid, "Quero uma casa para 6 pessoas de 10/12 a 15/12")
    await world.say(pnid, "Gostei da primeira, quero a primeira")
    async with tenant_session(app_sessions, tenant_id) as session:
        handoff = (await session.execute(select(Handoff))).scalar_one()
    assert handoff.reason == "choose"
    assert handoff.summary["casa_escolhida"] == "CASA05"
    assert handoff.summary["pedido"]["guests"] == 6
    assert "corretor" in world.gateway.sent[-1].body


async def test_llm_down_sends_contingency_and_hands_off(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    world = World(app_sessions, redis, keyring, FakeLLM(fail=True))
    await world.say(pnid, "Casa para 4 de 10/12 a 15/12")
    assert world.gateway.sent[0].body.endswith(CONTINGENCY)
    async with tenant_session(app_sessions, tenant_id) as session:
        assert (await session.execute(select(Handoff.reason))).scalar_one() == "llm_unavailable"


async def test_shadow_mode_records_without_sending(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring, TenantStatus.SHADOW)
    world = World(app_sessions, redis, keyring, FakeLLM())
    await world.say(pnid, "Casa para 4 de 10/12 a 15/12")
    assert world.gateway.sent == []
    async with tenant_session(app_sessions, tenant_id) as session:
        shadow = (await session.execute(select(AuditLog))).scalars().all()
    events = [a.event_type for a in shadow]
    assert "shadow_response" in events and "response_sent" not in events
    payload = next(a.payload for a in shadow if a.event_type == "shadow_response")
    assert LEGAL_NOTICE in payload["text"]


async def test_inactive_tenant_does_not_answer(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    _, pnid = await setup_tenant(app_sessions, ops_sessions, keyring, TenantStatus.ONBOARDING)
    world = World(app_sessions, redis, keyring, FakeLLM())
    outcome = await world.say(pnid, "Casa para 4 de 10/12 a 15/12")
    assert outcome.skipped == SkipReason.TENANT_INACTIVE
    assert world.gateway.sent == []


async def test_send_failure_keeps_messages_pending_for_retry(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    world = World(app_sessions, redis, keyring, FakeLLM())
    world.gateway.fail_with = GatewayError("API respondeu 503", retryable=True, status=503)
    with pytest.raises(GatewayError):
        await world.say(pnid, "Casa para 4 de 10/12 a 15/12")
    async with tenant_session(app_sessions, tenant_id) as session:
        pending = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.direction == "in", Message.processed_at.is_(None))
        )
    assert pending == 1
    world.gateway.fail_with = None
    tenant, conversation = await world.conversation()
    assert (await world.processor.process(tenant, conversation)).handled
    assert len(world.gateway.sent) == 1


async def test_stale_dates_never_claim_availability(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    async with tenant_session(app_sessions, tenant_id) as session:
        await session.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(availability_updated_at=datetime(2026, 11, 20, 14, 59, tzinfo=UTC))
        )  # 24 h e 1 min antes de NOW
    world = World(app_sessions, redis, keyring, FakeLLM())
    await world.say(pnid, "Casa para 4 de 10/12 a 15/12")
    body = world.gateway.sent[0].body
    assert "Disponibilidade a confirmar com o corretor." in body
    assert "disponível" not in body.lower()


class FallbackFakeLLM(FakeLLM):
    """Simula o fallback de recusa: o modelo pedido recusou e outro respondeu."""

    async def complete_json(self, **kwargs: Any) -> LLMResponse:
        response = await super().complete_json(**kwargs)
        return dataclasses.replace(response, model="claude-opus-5", fallback_from="claude-opus-5-5")


async def test_refusal_fallback_is_recorded_in_audit_log(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    world = World(app_sessions, redis, keyring, FallbackFakeLLM())
    await world.say(pnid, "Casa para 4 de 10/12 a 15/12")
    async with tenant_session(app_sessions, tenant_id) as session:
        audit = (
            await session.execute(select(AuditLog).where(AuditLog.event_type == "response_sent"))
        ).scalar_one()
    fallbacks = audit.payload["llm_refusal_fallback"]
    assert {"from": "claude-opus-5-5", "to": "claude-opus-5", "purpose": "extraction"} in fallbacks
    assert all(call["model"] == "claude-opus-5" for call in audit.payload["llm_calls"])


async def test_audit_lists_llm_calls_without_fallback(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    world = World(app_sessions, redis, keyring, FakeLLM())
    await world.say(pnid, "Casa para 4 de 10/12 a 15/12")
    async with tenant_session(app_sessions, tenant_id) as session:
        audit = (
            await session.execute(select(AuditLog).where(AuditLog.event_type == "response_sent"))
        ).scalar_one()
    assert [c["purpose"] for c in audit.payload["llm_calls"]] == ["extraction", "writer"]
    assert audit.payload["llm_refusal_fallback"] is None
