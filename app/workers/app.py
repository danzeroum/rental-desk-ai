"""Montagem do processo `worker` a partir da configuração."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.adapters.factory import build_gateway, build_keyring, build_llm, build_transcriber
from app.core.config import Settings, get_settings
from app.core.db import create_engine
from app.core.redis import create_redis
from app.core.rls import scoped_session_factory
from app.domain.messages import RATE_LIMIT_NOTICE
from app.main import build_queue
from app.workers.engine import ConversationEngine
from app.workers.pipeline import ResponsePipeline
from app.workers.processor import BatchHandler, ConversationProcessor
from app.workers.queue import Job
from app.workers.responder import send_reply
from app.workers.runner import Worker


async def run_worker(
    stop: asyncio.Event, settings: Settings | None = None, handler: BatchHandler | None = None
) -> None:
    settings = settings or get_settings()
    engine = create_engine(settings)
    redis = create_redis(settings)
    sessions = scoped_session_factory(engine)
    queue = build_queue(redis, settings)
    gateway, keyring = build_gateway(settings), build_keyring(settings)
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
        await Worker(queue, processor, send_notice).run(stop)
    finally:
        await redis.aclose()
        await engine.dispose()
