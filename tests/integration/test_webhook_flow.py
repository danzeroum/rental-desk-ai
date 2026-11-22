"""Fluxo do M4 de ponta a ponta: webhook → banco → debounce → worker."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.transcriber import FakeTranscriber
from app.adapters.whatsapp import FakeWhatsAppGateway, Media
from app.core.config import Settings
from app.core.crypto import Keyring
from app.core.rls import tenant_session
from app.domain.messages import RATE_LIMIT_NOTICE
from app.main import create_app
from app.models import (
    AuditLog,
    Contact,
    Conversation,
    Message,
    Tenant,
    TenantRoute,
    WhatsAppAccount,
)
from app.models.enums import MessageKind, MessageOrigin
from app.workers.channel import seal_token
from app.workers.processor import ConversationProcessor, RecordingHandler, SkipReason
from app.workers.queue import DUE, Job, WorkQueue
from app.workers.responder import send_reply
from app.workers.runner import Worker
from tests.factories import create_tenant

pytestmark = pytest.mark.integration

Sessions = async_sessionmaker[AsyncSession]
GUEST = "5511988887777"


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


@pytest.fixture
async def tenant(
    app_sessions: Sessions, ops_sessions: Sessions, keyring: Keyring
) -> tuple[uuid.UUID, str]:
    tenant_id = await create_tenant(ops_sessions)
    pnid = f"PN{uuid.uuid4().hex[:10]}"
    async with tenant_session(app_sessions, tenant_id) as session:
        session.add(TenantRoute(channel="whatsapp", external_id=pnid, tenant_id=tenant_id))
        session.add(
            WhatsAppAccount(
                tenant_id=tenant_id, phone_number_id=pnid, display_phone="+551130000000",
                access_token_enc=seal_token(keyring, tenant_id, "token-do-cliente"),
            )
        )  # fmt: skip
    return tenant_id, pnid


@pytest.fixture
def client(settings: Settings, redis: Redis) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def message(mid: str, body: str = "Oi", **extra: Any) -> dict[str, Any]:
    item = {
        "from": GUEST,
        "id": mid,
        "timestamp": str(int(time.time())),
        "type": "text",
        "text": {"body": body},
    }
    item.update(extra)
    return item


def webhook(pnid: str, *messages: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "W", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "551130000000", "phone_number_id": pnid},
            "contacts": [{"profile": {"name": "Ana"}, "wa_id": GUEST}],
            "messages": list(messages),
        }}]}],
    }  # fmt: skip


def post(
    client: TestClient, settings: Settings, payload: dict[str, Any], *, secret: str | None = None
) -> int:
    body = json.dumps(payload).encode()
    key = secret or settings.whatsapp_app_secret.get_secret_value()  # type: ignore[union-attr]
    signature = "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"content-type": "application/json", "x-hub-signature-256": signature},
    )
    return response.status_code


async def count(sessions: Sessions, tenant_id: uuid.UUID, model: Any) -> int:
    async with tenant_session(sessions, tenant_id) as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def processor(
    sessions: Sessions, queue: WorkQueue, keyring: Keyring, handler: RecordingHandler,
    gateway: FakeWhatsAppGateway | None = None, transcriber: FakeTranscriber | None = None,
) -> ConversationProcessor:  # fmt: skip
    return ConversationProcessor(
        sessions=sessions, queue=queue, gateway=gateway or FakeWhatsAppGateway(),
        transcriber=transcriber or FakeTranscriber(), keyring=keyring, handler=handler,
    )  # fmt: skip


async def _unused_notice(job: Job) -> None:
    raise AssertionError("não deveria avisar")


def later_queue(redis: Redis, seconds: float = 30) -> WorkQueue:
    """Fila com o relógio adiantado, para vencer o debounce sem esperar."""
    return WorkQueue(redis, clock=lambda: time.time() + seconds)


# --- Webhook -------------------------------------------------------------------------


def test_verification_challenge(client: TestClient) -> None:
    ok = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "token-de-verificacao",
            "hub.challenge": "42",
        },
    )
    assert (ok.status_code, ok.text) == (200, "42")
    bad = client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "42"},
    )
    assert bad.status_code == 403


async def test_invalid_signature_is_rejected_and_nothing_stored(
    client: TestClient, settings: Settings, tenant: tuple[uuid.UUID, str], app_sessions: Sessions
) -> None:
    tenant_id, pnid = tenant
    assert post(client, settings, webhook(pnid, message("wamid.x")), secret="outro") == 401
    assert await count(app_sessions, tenant_id, Message) == 0


async def test_message_is_stored_once_and_scheduled(
    client: TestClient,
    settings: Settings,
    tenant: tuple[uuid.UUID, str],
    app_sessions: Sessions,
    redis: Redis,
) -> None:
    tenant_id, pnid = tenant
    payload = webhook(pnid, message("wamid.1", "Casa para 12 no Réveillon"))
    assert post(client, settings, payload) == 200
    assert post(client, settings, payload) == 200  # reenvio da Meta (RF01)
    assert await count(app_sessions, tenant_id, Message) == 1
    async with tenant_session(app_sessions, tenant_id) as session:
        contact = (await session.execute(select(Contact))).scalar_one()
        stored = (await session.execute(select(Message))).scalar_one()
        conversation = (await session.execute(select(Conversation))).scalar_one()
    assert contact.phone_e164 == f"+{GUEST}"
    assert (stored.body, stored.origin, stored.processed_at) == (
        "Casa para 12 no Réveillon", MessageOrigin.GUEST, None,
    )  # fmt: skip
    assert conversation.first_inbound_at is not None
    assert await redis.zcard(DUE) == 1


async def test_unknown_number_is_ignored(
    client: TestClient, settings: Settings, redis: Redis
) -> None:
    assert post(client, settings, webhook("NUMERO-DE-NINGUEM", message("wamid.z"))) == 200
    assert await redis.zcard(DUE) == 0


def test_oversized_body(client: TestClient) -> None:
    response = client.post("/webhooks/whatsapp", content=b"x" * 1_000_001)
    assert response.status_code == 413


def test_invalid_json_with_valid_signature(client: TestClient, settings: Settings) -> None:
    body = b"{nao-json"
    key = settings.whatsapp_app_secret.get_secret_value()  # type: ignore[union-attr]
    signature = "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": signature}
    )
    assert response.status_code == 400


# --- Debounce + worker -----------------------------------------------------------------


async def test_burst_of_5_messages_produces_one_batch(
    client: TestClient, settings: Settings, tenant: tuple[uuid.UUID, str],
    app_sessions: Sessions, redis: Redis, keyring: Keyring,
) -> None:  # fmt: skip
    """Aceite do M4: rajada de 5 mensagens → 1 processamento, com as 5 juntas."""
    tenant_id, pnid = tenant
    for i, text in enumerate(
        ["oi", "queria uma casa", "para 12 pessoas", "no réveillon", "com piscina"]
    ):
        assert post(client, settings, webhook(pnid, message(f"wamid.b{i}", text))) == 200
    queue = later_queue(redis)
    handler = RecordingHandler()

    async def no_notice(job: Job) -> None:
        raise AssertionError("não deveria avisar")

    worker = Worker(queue, processor(app_sessions, queue, keyring, handler), no_notice)
    await queue.ensure_groups()
    assert await worker.tick(block_ms=10) == 1
    assert await worker.tick(block_ms=10) == 0
    (batch,) = handler.batches
    assert batch.combined_text == "oi\nqueria uma casa\npara 12 pessoas\nno réveillon\ncom piscina"
    async with tenant_session(app_sessions, tenant_id) as session:
        pending = await session.scalar(
            select(func.count()).select_from(Message).where(Message.processed_at.is_(None))
        )
    assert pending == 0


async def test_audio_is_transcribed_and_media_reference_dropped(
    client: TestClient, settings: Settings, tenant: tuple[uuid.UUID, str],
    app_sessions: Sessions, redis: Redis, keyring: Keyring,
) -> None:  # fmt: skip
    tenant_id, pnid = tenant
    audio = {
        "from": GUEST,
        "id": "wamid.audio",
        "timestamp": str(int(time.time())),
        "type": "audio",
        "audio": {"id": "MEDIA-1", "mime_type": "audio/ogg; codecs=opus"},
    }
    assert post(client, settings, webhook(pnid, audio)) == 200
    queue = later_queue(redis)
    await queue.ensure_groups()
    gateway = FakeWhatsAppGateway(media={"MEDIA-1": Media(b"OGG", "audio/ogg")})
    handler = RecordingHandler()
    proc = processor(
        app_sessions, queue, keyring, handler, gateway, FakeTranscriber("casa para seis")
    )
    await queue.pump()
    (job,) = await queue.read("t", block_ms=10)
    await proc.process(job.tenant_id, job.conversation_id)
    assert handler.batches[0].messages[0].text == "casa para seis"
    async with tenant_session(app_sessions, tenant_id) as session:
        stored = (await session.execute(select(Message))).scalar_one()
    assert (stored.kind, stored.transcript, stored.media_ref) == (
        MessageKind.AUDIO,
        "casa para seis",
        None,
    )


async def test_failed_transcription_is_flagged(
    client: TestClient, settings: Settings, tenant: tuple[uuid.UUID, str],
    app_sessions: Sessions, redis: Redis, keyring: Keyring,
) -> None:  # fmt: skip
    _, pnid = tenant
    audio = {
        "from": GUEST,
        "id": "wamid.a2",
        "type": "audio",
        "audio": {"id": "M2", "mime_type": "audio/ogg"},
    }
    post(client, settings, webhook(pnid, audio))
    queue = later_queue(redis)
    await queue.ensure_groups()
    handler = RecordingHandler()
    proc = processor(
        app_sessions, queue, keyring, handler,
        FakeWhatsAppGateway(media={"M2": Media(b"x", "audio/ogg")}), FakeTranscriber(fail=True),
    )  # fmt: skip
    await queue.pump()
    (job,) = await queue.read("t", block_ms=10)
    await proc.process(job.tenant_id, job.conversation_id)
    (msg,) = handler.batches[0].messages
    assert (msg.text, msg.transcription_failed) == (None, True)


@pytest.mark.parametrize("gate", ["paused", "muted", "opted_out"])
async def test_gates_skip_response_but_mark_processed(
    client: TestClient, settings: Settings, tenant: tuple[uuid.UUID, str],
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring, gate: str,
) -> None:  # fmt: skip
    tenant_id, pnid = tenant
    post(client, settings, webhook(pnid, message("wamid.g")))
    now = datetime.now(UTC)
    if gate == "paused":
        async with tenant_session(app_sessions, tenant_id) as session:
            await session.execute(
                update(Tenant).where(Tenant.id == tenant_id).values(paused_at=now)
            )
    elif gate == "muted":
        async with tenant_session(app_sessions, tenant_id) as session:
            await session.execute(
                update(Conversation).values(muted_until=now + timedelta(hours=24))
            )
    else:
        async with tenant_session(app_sessions, tenant_id) as session:
            await session.execute(update(Contact).values(opted_out_at=now))
    queue = later_queue(redis)
    await queue.ensure_groups()
    handler = RecordingHandler()
    await queue.pump()
    (job,) = await queue.read("t", block_ms=10)
    outcome = await processor(app_sessions, queue, keyring, handler).process(
        job.tenant_id, job.conversation_id
    )
    expected = {
        "paused": SkipReason.TENANT_PAUSED,
        "muted": SkipReason.MUTED,
        "opted_out": SkipReason.OPTED_OUT,
    }
    assert outcome.skipped == expected[gate]
    assert handler.batches == []
    async with tenant_session(app_sessions, tenant_id) as session:
        assert (await session.execute(select(Message.processed_at))).scalar_one() is not None


async def test_rate_limit_sends_one_polite_notice_and_audits(
    client: TestClient, settings: Settings, tenant: tuple[uuid.UUID, str],
    app_sessions: Sessions, redis: Redis, keyring: Keyring,
) -> None:  # fmt: skip
    tenant_id, pnid = tenant
    for i in range(17):
        assert post(client, settings, webhook(pnid, message(f"wamid.r{i}", f"msg {i}"))) == 200
    queue = later_queue(redis)
    await queue.ensure_groups()
    gateway = FakeWhatsAppGateway()

    async def send_notice(job: Any) -> None:
        await send_reply(
            app_sessions,
            gateway,
            keyring,
            tenant_id=job.tenant_id,
            conversation_id=job.conversation_id,
            text=RATE_LIMIT_NOTICE,
            event_type="rate_limit_notice",
            audit_payload={},
            snapshot_hash=None,
            now=datetime.now(UTC),
        )

    handler = RecordingHandler()
    worker = Worker(queue, processor(app_sessions, queue, keyring, handler), send_notice)
    while await worker.tick(block_ms=10):
        pass
    assert [s.body for s in gateway.sent] == [RATE_LIMIT_NOTICE]
    assert gateway.sent[0].to == GUEST
    assert gateway.sent[0].channel.access_token == "token-do-cliente"
    assert handler.batches == []  # em silêncio: o lote não é respondido
    async with tenant_session(app_sessions, tenant_id) as session:
        events = (await session.execute(select(AuditLog.event_type))).scalars().all()
        outbound = (
            await session.execute(select(Message).where(Message.origin == MessageOrigin.ASSISTANT))
        ).scalar_one()
    assert events == ["rate_limit_notice"]
    assert outbound.body == RATE_LIMIT_NOTICE


async def test_locked_conversation_is_rescheduled(
    client: TestClient, settings: Settings, tenant: tuple[uuid.UUID, str],
    app_sessions: Sessions, redis: Redis, keyring: Keyring,
) -> None:  # fmt: skip
    _, pnid = tenant
    post(client, settings, webhook(pnid, message("wamid.l")))
    queue = later_queue(redis)
    await queue.ensure_groups()
    handler = RecordingHandler()
    await queue.pump()
    (job,) = await queue.read("t", block_ms=10)
    await queue.redis.xadd(job.stream, {"kind": job.kind, "key": job.key})  # devolve à fila
    token = await queue.lock(job.key)
    worker = Worker(
        queue, processor(app_sessions, queue, keyring, handler), send_notice=_unused_notice
    )
    assert await worker.tick(block_ms=10) == 1
    assert handler.batches == []
    assert await redis.zscore(DUE, job.key) is not None
    assert token is not None
