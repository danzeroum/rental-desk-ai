# ruff: noqa: F811 - as fixtures `redis` e `keyring` vêm de test_pipeline
"""Status de entrega (enviada, entregue, lida, falhou) gravado na mensagem e na trilha imutável."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.adapters.fake_llm import FakeLLM
from app.adapters.whatsapp_webhook import StatusUpdate, WebhookBatch, parse_webhook
from app.core.crypto import Keyring
from app.core.rls import tenant_session
from app.models import AuditLog, Message
from app.models.enums import Direction
from app.workers.ingest import ingest_batch
from tests.integration.test_pipeline import (  # noqa: F401 - fixtures
    NOW,
    Sessions,
    World,
    keyring,
    redis,
    setup_tenant,
)

pytestmark = pytest.mark.integration


def _status(pnid: str, wamid: str, status: str, minutes: int, **extra: Any) -> StatusUpdate:
    return StatusUpdate(pnid, wamid, status, NOW + timedelta(minutes=minutes), **extra)


async def test_delivery_statuses_advance_and_are_audited(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    world = World(app_sessions, redis, keyring, FakeLLM())
    await world.say(pnid, "Casa para 8 pessoas de 28/12 a 02/01")
    wamid = world.gateway.sent[-1].external_id

    batch = WebhookBatch(statuses=[
        _status(pnid, wamid, "sent", 1),
        _status(pnid, wamid, "read", 3),
        _status(pnid, wamid, "delivered", 2),  # fora de ordem: não volta de lida para entregue
        _status(pnid, "wamid.desconhecido", "delivered", 2),
    ])  # fmt: skip
    result = await ingest_batch(batch, sessions=app_sessions, queue=world.queue, now=NOW)
    assert result.statuses == 2

    async with tenant_session(app_sessions, tenant_id) as session:
        message = await session.scalar(select(Message).where(Message.external_id == wamid))
        events = list(await session.scalars(
            select(AuditLog).where(AuditLog.event_type.in_(
                ["delivery_status", "message_received"])).order_by(AuditLog.seq)
        ))  # fmt: skip
    assert message is not None and message.delivery_status == "read"
    assert message.delivery_status_at == NOW + timedelta(minutes=3)
    kinds = [(e.event_type, e.payload.get("status")) for e in events]
    assert kinds == [("message_received", None), ("delivery_status", "sent"),
                     ("delivery_status", "read")]  # fmt: skip


async def test_failed_delivery_keeps_meta_code_without_personal_data(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    world = World(app_sessions, redis, keyring, FakeLLM())
    await world.say(pnid, "Oi, casa para 4 pessoas de 10/01 a 15/01")
    wamid = world.gateway.sent[-1].external_id
    payload = {"entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": pnid},
        "statuses": [{"id": wamid, "status": "failed", "timestamp": "1795543749",
                      "recipient_id": "5511977776666",
                      "errors": [{"code": 130497, "title": "Business account is restricted "
                                  "from messaging users in this country. ana@x.com"}]}],
    }}]}]}  # fmt: skip
    await ingest_batch(parse_webhook(payload), sessions=app_sessions, queue=world.queue, now=NOW)
    async with tenant_session(app_sessions, tenant_id) as session:
        message = await session.scalar(
            select(Message).where(Message.direction == Direction.OUTBOUND,
                                  Message.external_id == wamid)
        )  # fmt: skip
        event = await session.scalar(
            select(AuditLog).where(AuditLog.event_type == "delivery_status")
        )
    assert message is not None and message.delivery_status == "failed"
    assert message.delivery_error_code == 130497 and "restricted" in (message.delivery_error or "")
    assert event is not None and event.payload["error_code"] == 130497
    stored = str(event.payload) + (message.delivery_error or "")
    assert "ana@x.com" not in stored and "5511977776666" not in stored
