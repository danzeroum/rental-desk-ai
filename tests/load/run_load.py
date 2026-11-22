"""Teste de carga (SPEC seção 11): 200 conversas simultâneas em 10 imobiliárias, sem perda
e com p95 ≤ 15 s depois da janela de debounce.

Usa Postgres e Redis reais, o mesmo pipeline do worker e vários laços concorrentes
(WORKER_CONCURRENCY). A IA e o WhatsApp são falsos, com latência simulada (a IA real
é medida à parte pelo `make golden-live`).

    uv run python -m tests.load.run_load --tenants 10 --conversations 200
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.fake_llm import FakeLLM
from app.adapters.llm import LLMResponse
from app.adapters.mailer import FakeMailer
from app.adapters.transcriber import FakeTranscriber
from app.adapters.whatsapp import Channel, FakeWhatsAppGateway
from app.adapters.whatsapp_webhook import InboundMessage, WebhookBatch
from app.core.crypto import Keyring
from app.core.db import async_url
from app.core.rls import scoped_session_factory
from app.main import build_queue
from app.models.enums import MessageKind
from app.workers.app import worker_pool_size
from app.workers.engine import ConversationEngine
from app.workers.handoffs import HandoffService
from app.workers.ingest import ingest_batch
from app.workers.pipeline import ResponsePipeline
from app.workers.processor import ConversationProcessor
from app.workers.queue import DEAD_LETTER
from app.workers.runner import Worker
from tests.conftest import TEST_DATABASE_OPS_URL, TEST_DATABASE_URL, make_settings
from tests.integration.test_pipeline import setup_tenant

P95_TARGET_SECONDS = 15.0
MESSAGES = (
    ("Oi, boa tarde", "Casa para 4 de 10/12 a 15/12"),
    ("Somos 6 pessoas", "de 05/01 a 09/01, com piscina"),
    ("Quero uma casa para 8 pessoas de 28/12 a 02/01",),
)


class SlowLLM:
    """FakeLLM com a latência de um modelo rápido (0,8 a 2,5 s por chamada)."""

    def __init__(self, low: float, high: float) -> None:
        self.inner, self.low, self.high = FakeLLM(), low, high

    async def complete_json(self, **kwargs: Any) -> LLMResponse:
        await asyncio.sleep(random.uniform(self.low, self.high))  # noqa: S311 - simulação
        return await self.inner.complete_json(**kwargs)


class TimedGateway(FakeWhatsAppGateway):
    def __init__(self) -> None:
        super().__init__()
        self.first_reply: dict[str, float] = {}

    async def send_text(self, channel: Channel, to: str, body: str) -> str:
        await asyncio.sleep(0.2)
        self.first_reply.setdefault(to, time.monotonic())
        return await super().send_text(channel, to, body)


@dataclass
class Result:
    conversations: int
    answered: int
    dead_jobs: int
    latencies: list[float]
    wall_seconds: float

    def percentile(self, pct: float) -> float:
        ordered = sorted(self.latencies)
        return ordered[max(round(pct / 100 * len(ordered)) - 1, 0)] if ordered else 0.0

    @property
    def passed(self) -> bool:
        complete = self.answered == self.conversations and self.dead_jobs == 0
        return complete and self.percentile(95) <= P95_TARGET_SECONDS

    def report(self) -> str:
        p95, verdict = self.percentile(95), "APROVADO" if self.passed else "REPROVADO"
        median, worst = statistics.median(self.latencies), max(self.latencies)
        return (
            f"Conversas: {self.conversations} · respondidas: {self.answered} · "
            f"fila de falhas: {self.dead_jobs}\n"
            f"Tempo até a resposta, depois do debounce: mediana {median:.1f} s · "
            f"p95 {p95:.1f} s · máximo {worst:.1f} s (meta p95 ≤ {P95_TARGET_SECONDS:.0f} s)\n"
            f"Duração total: {self.wall_seconds:.0f} s · resultado: {verdict}"
        )


async def _send(pnid: str, phone: str, texts: Sequence[str], sessions: Any, queue: Any) -> float:
    last = 0.0
    for text in texts:
        message = InboundMessage(
            phone_number_id=pnid, external_id=f"wamid.load.{uuid.uuid4().hex}", wa_id=phone,
            kind=MessageKind.TEXT, timestamp=datetime.now(UTC), text=text,
        )  # fmt: skip
        await ingest_batch(
            WebhookBatch(messages=[message]), sessions=sessions, queue=queue, now=datetime.now(UTC)
        )
        last = time.monotonic()
        await asyncio.sleep(random.uniform(0.3, 1.5))  # noqa: S311 - pessoa digitando
    return last


async def run(
    tenants: int, conversations: int, concurrency: int, quiet: float, llm: tuple[float, float]
) -> Result:
    settings = make_settings(debounce_quiet_seconds=quiet, debounce_max_seconds=max(quiet * 3, 6))
    keyring = Keyring.parse(settings.data_encryption_keys.get_secret_value())
    pool = worker_pool_size(settings.model_copy(update={"worker_concurrency": concurrency}))
    app_engine = create_async_engine(async_url(TEST_DATABASE_URL), pool_size=pool)
    ops_engine = create_async_engine(async_url(TEST_DATABASE_OPS_URL), poolclass=NullPool)
    sessions = scoped_session_factory(app_engine)
    ops = async_sessionmaker(ops_engine, expire_on_commit=False)
    redis = Redis.from_url(str(settings.redis_url), decode_responses=True)
    await redis.flushdb()
    places = [await setup_tenant(sessions, ops, keyring) for _ in range(tenants)]
    gateway = TimedGateway()
    queue = build_queue(redis, settings)
    handoffs = HandoffService(
        sessions=sessions, gateway=gateway, keyring=keyring, mailer=FakeMailer()
    )
    pipeline = ResponsePipeline(
        sessions=sessions, engine=ConversationEngine(SlowLLM(*llm)), gateway=gateway,
        keyring=keyring, public_base_url="https://carga", handoffs=handoffs,
    )  # fmt: skip
    processor = ConversationProcessor(
        sessions=sessions, queue=queue, gateway=gateway, transcriber=FakeTranscriber(),
        keyring=keyring, handler=pipeline, members=handoffs,
    )  # fmt: skip

    async def no_notice(_: Any) -> None:
        return None

    stop = asyncio.Event()
    loops = [
        asyncio.create_task(Worker(queue, processor, no_notice, consumer=f"carga-{i}").run(stop))
        for i in range(concurrency)
    ]
    phones = [f"55119{uuid.uuid4().int % 10**8:08d}" for _ in range(conversations)]
    started = time.monotonic()

    async def guest(index: int) -> float:
        await asyncio.sleep(random.uniform(0, 3))  # noqa: S311 - chegam quase juntos
        pnid = places[index % tenants][1]
        return await _send(pnid, phones[index], MESSAGES[index % len(MESSAGES)], sessions, queue)

    last_sent = await asyncio.gather(*(guest(i) for i in range(conversations)))
    deadline = time.monotonic() + 300
    while len(gateway.first_reply) < conversations and time.monotonic() < deadline:  # noqa: ASYNC110
        await asyncio.sleep(0.5)  # espera ativa curta: o gateway falso não sinaliza conclusão
    stop.set()
    await asyncio.gather(*loops)
    latencies = [
        max(gateway.first_reply[phone] - (sent + quiet), 0.0)
        for phone, sent in zip(phones, last_sent, strict=True)
        if phone in gateway.first_reply
    ]
    dead = int(await redis.xlen(DEAD_LETTER))
    await redis.aclose()
    await app_engine.dispose()
    await ops_engine.dispose()
    return Result(conversations, len(latencies), dead, latencies, time.monotonic() - started)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenants", type=int, default=10)
    parser.add_argument("--conversations", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=40)
    parser.add_argument("--quiet", type=float, default=6.0, help="janela de debounce (s)")
    parser.add_argument("--llm-min", type=float, default=0.8)
    parser.add_argument("--llm-max", type=float, default=2.5)
    args = parser.parse_args()
    result = asyncio.run(
        run(
            args.tenants,
            args.conversations,
            args.concurrency,
            args.quiet,
            (args.llm_min, args.llm_max),
        )
    )
    print(result.report())  # noqa: T201
    raise SystemExit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
