"""Fila em Redis real: debounce (RF05), prioridade e limite anti-abuso (RF20)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from app.core.config import Settings
from app.workers.queue import (
    DEAD_LETTER,
    STREAM_NORMAL,
    STREAM_PRIORITY,
    RateDecision,
    WorkQueue,
    conversation_key,
)

pytestmark = pytest.mark.integration


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def queue(redis: Redis, clock: Clock) -> WorkQueue:
    q = WorkQueue(redis, quiet_seconds=6, max_seconds=20, rate_limit_per_minute=15, clock=clock)
    await q.ensure_groups()
    return q


def key() -> str:
    return conversation_key(uuid.uuid4(), uuid.uuid4())


async def test_burst_of_5_messages_in_10s_produces_one_job(queue: WorkQueue, clock: Clock) -> None:
    """Aceite do M4 / RF05: rajada de 5 mensagens → 1 processamento."""
    k = key()
    for offset in (0, 2, 4, 6, 8):
        clock.now = 1_000_000 + offset
        await queue.schedule(k)
    clock.now = 1_000_000 + 13.9  # 6 s depois da última ainda não passou
    assert await queue.pump() == 0
    clock.now = 1_000_000 + 14
    assert await queue.pump() == 1
    jobs = await queue.read("w1", block_ms=10)
    assert [(j.kind, j.key) for j in jobs] == [("conversation", k)]
    clock.now += 100
    assert await queue.pump() == 0  # não duplica


async def test_long_burst_is_capped_at_20s(queue: WorkQueue, clock: Clock) -> None:
    k = key()
    start = clock.now
    for offset in range(0, 30, 5):  # mensagem a cada 5 s por 25 s
        clock.now = start + offset
        if offset <= 20:
            await queue.schedule(k)
        if clock.now >= start + 20:
            break
    clock.now = start + 20
    assert await queue.pump() == 1


async def test_after_processing_new_message_starts_new_batch(
    queue: WorkQueue, clock: Clock
) -> None:
    k = key()
    await queue.schedule(k)
    clock.now += 6
    assert await queue.pump() == 1
    clock.now += 30
    due = await queue.schedule(k)
    assert due == clock.now + 6  # janela máxima recomeça


async def test_new_contact_goes_to_priority_stream(
    queue: WorkQueue, clock: Clock, redis: Redis
) -> None:
    new, old = key(), key()
    await queue.schedule(old)
    await queue.schedule(new, priority=True)
    clock.now += 6
    assert await queue.pump() == 2
    assert await redis.xlen(STREAM_PRIORITY) == 1
    assert await redis.xlen(STREAM_NORMAL) == 1
    first = await queue.read("w1", count=1, block_ms=10)
    assert first[0].key == new


async def test_ack_removes_job_and_job_exposes_ids(
    queue: WorkQueue, clock: Clock, redis: Redis
) -> None:
    tenant, conversation = uuid.uuid4(), uuid.uuid4()
    await queue.enqueue("notice", conversation_key(tenant, conversation))
    (job,) = await queue.read("w1", block_ms=10)
    assert (job.kind, job.tenant_id, job.conversation_id) == ("notice", tenant, conversation)
    await queue.ack(job)
    assert await redis.xlen(STREAM_NORMAL) == 0
    assert await queue.reclaim("w2", min_idle_ms=0) == []


async def test_unacked_job_is_reclaimed_then_dead_lettered(queue: WorkQueue, redis: Redis) -> None:
    await queue.enqueue("conversation", key())
    (job,) = await queue.read("w1", block_ms=10)
    for attempt in range(2, 4):
        (again,) = await queue.reclaim("w2", min_idle_ms=0)
        assert (again.job_id, again.deliveries) == (job.job_id, attempt)
    assert await queue.reclaim("w2", min_idle_ms=0) == []
    assert await redis.xlen(DEAD_LETTER) == 1


async def test_conversation_lock(queue: WorkQueue) -> None:
    k = key()
    token = await queue.lock(k)
    assert token is not None
    assert await queue.lock(k) is None
    await queue.unlock(k, "outro-token")  # não solta a trava de outro processo
    assert await queue.lock(k) is None
    await queue.unlock(k, token)
    assert await queue.lock(k) is not None


async def test_rate_limit_15_per_minute_then_10_min_silence(queue: WorkQueue, clock: Clock) -> None:
    tenant, contact = uuid.uuid4(), uuid.uuid4()
    decisions = []
    for i in range(17):
        clock.now += 1
        decisions.append(await queue.register_inbound(tenant, contact, f"m{i}"))
    assert decisions[:15] == [RateDecision.OK] * 15
    assert decisions[15] == RateDecision.JUST_SILENCED  # 16ª: um aviso só
    assert decisions[16] == RateDecision.SILENCED
    assert await queue.is_silenced(tenant, contact)


async def test_rate_limit_window_slides(queue: WorkQueue, clock: Clock) -> None:
    tenant, contact = uuid.uuid4(), uuid.uuid4()
    for i in range(15):
        clock.now += 3  # 15 mensagens em 45 s
        assert await queue.register_inbound(tenant, contact, f"a{i}") is RateDecision.OK
    clock.now += 61
    assert await queue.register_inbound(tenant, contact, "depois") is RateDecision.OK


async def test_rate_limit_is_per_contact(queue: WorkQueue, clock: Clock) -> None:
    tenant = uuid.uuid4()
    noisy, calm = uuid.uuid4(), uuid.uuid4()
    for i in range(16):
        await queue.register_inbound(tenant, noisy, f"n{i}")
    assert await queue.register_inbound(tenant, calm, "c") is RateDecision.OK
