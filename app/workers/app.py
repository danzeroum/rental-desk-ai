"""Montagem do processo `worker` a partir da configuração."""

from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime

from app.adapters.factory import (
    build_gateway,
    build_keyring,
    build_llm,
    build_mailer,
    build_transcriber,
)
from app.core.config import Settings, get_settings
from app.core.db import create_engine
from app.core.redis import create_redis
from app.core.rls import scoped_session_factory
from app.domain.messages import RATE_LIMIT_NOTICE
from app.main import build_queue
from app.workers.engine import ConversationEngine
from app.workers.handoffs import HandoffService
from app.workers.pipeline import ResponsePipeline
from app.workers.processor import BatchHandler, ConversationProcessor
from app.workers.queue import Job
from app.workers.reminders import MemberCommands, ReminderService
from app.workers.responder import send_reply
from app.workers.runner import Worker


def worker_pool_size(settings: Settings) -> int:
    """Conexões do worker: as conversas só usam o banco em transações curtas (a espera da IA
    fica fora delas), então metade dos laços basta; medido no teste de carga (docs/carga.md)."""
    return max(settings.db_pool_size, settings.worker_concurrency // 2 + 2)


async def run_worker(
    stop: asyncio.Event, settings: Settings | None = None, handler: BatchHandler | None = None
) -> None:
    settings = settings or get_settings()
    pool = worker_pool_size(settings)
    engine = create_engine(settings.model_copy(update={"db_pool_size": pool}))
    redis = create_redis(settings)
    sessions = scoped_session_factory(engine)
    queue = build_queue(redis, settings)
    gateway, keyring = build_gateway(settings, redis), build_keyring(settings)
    handoffs = HandoffService(
        sessions=sessions, gateway=gateway, keyring=keyring, mailer=build_mailer(settings)
    )
    processor = ConversationProcessor(
        sessions=sessions,
        queue=queue,
        gateway=gateway,
        transcriber=build_transcriber(settings),
        keyring=keyring,
        handler=handler
        or ResponsePipeline(
            sessions=sessions,
            engine=ConversationEngine(build_llm(settings)),
            gateway=gateway,
            keyring=keyring,
            public_base_url=settings.public_base_url,
            handoffs=handoffs,
        ),
        members=MemberCommands(
            ReminderService(
                sessions=sessions,
                gateway=gateway,
                keyring=keyring,
                public_base_url=settings.public_base_url,
            ),
            handoffs,
        ),
    )

    async def send_notice(job: Job) -> None:
        await send_reply(
            sessions, gateway, keyring,
            tenant_id=job.tenant_id, conversation_id=job.conversation_id,
            text=RATE_LIMIT_NOTICE, event_type="rate_limit_notice",
            audit_payload={"reason": "anti_abuso"}, snapshot_hash=None, now=datetime.now(UTC),
        )  # fmt: skip

    try:
        host = socket.gethostname()
        loops = [
            Worker(queue, processor, send_notice, consumer=f"{host}-{index}").run(stop)
            for index in range(settings.worker_concurrency)
        ]
        await asyncio.gather(*loops)
    finally:
        await redis.aclose()
        await engine.dispose()
