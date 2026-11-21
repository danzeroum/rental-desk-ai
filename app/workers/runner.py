"""Laço do worker: move o debounce vencido para a fila, consome e confirma trabalhos.

Um trabalho só é confirmado (XACK) depois de concluído; se o worker cair no meio,
outro o retoma (`WorkQueue.reclaim`), e após 3 entregas ele vai para a fila morta.
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Final

from app.core.logging import bind_context, clear_context, get_logger
from app.workers.ingest import NOTICE_JOB
from app.workers.processor import ConversationProcessor
from app.workers.queue import RETRY_LOCKED_SECONDS, Job, WorkQueue

RECLAIM_EVERY_SECONDS: Final = 30.0
CONVERSATION_JOB: Final = "conversation"

log = get_logger(__name__)

NoticeSender = Callable[[Job], Awaitable[None]]


class Worker:
    def __init__(
        self,
        queue: WorkQueue,
        processor: ConversationProcessor,
        send_notice: NoticeSender,
        *,
        consumer: str | None = None,
    ) -> None:
        self.queue, self.processor, self.send_notice = queue, processor, send_notice
        self.consumer = consumer or f"{socket.gethostname()}-{id(self):x}"
        self._last_reclaim = 0.0

    async def run(self, stop: asyncio.Event) -> None:
        await self.queue.ensure_groups()
        log.info("worker_pronto", consumer=self.consumer)
        while not stop.is_set():
            await self.tick(block_ms=500)

    async def tick(self, *, block_ms: int = 500) -> int:
        """Uma volta do laço; devolve quantos trabalhos foram concluídos."""
        await self.queue.pump()
        jobs = await self.queue.read(self.consumer, block_ms=block_ms)
        if time.monotonic() - self._last_reclaim > RECLAIM_EVERY_SECONDS:
            self._last_reclaim = time.monotonic()
            jobs += await self.queue.reclaim(self.consumer)
        done = 0
        for job in jobs:
            done += await self._run_job(job)
        return done

    async def _run_job(self, job: Job) -> int:
        clear_context()
        bind_context(tenant_id=str(job.tenant_id), conversation_id=str(job.conversation_id))
        try:
            if job.kind == CONVERSATION_JOB:
                await self._conversation(job)
            elif job.kind == NOTICE_JOB:
                await self.send_notice(job)
            else:
                log.error("trabalho_desconhecido", kind=job.kind)
        except Exception:
            log.exception("trabalho_falhou", kind=job.kind, deliveries=job.deliveries)
            return 0  # sem ACK: outro worker retoma depois
        await self.queue.ack(job)
        return 1

    async def _conversation(self, job: Job) -> None:
        token = await self.queue.lock(job.key)
        if token is None:
            # Outro lote desta conversa está em andamento; tenta de novo em instantes.
            await self.queue.schedule(job.key, delay=RETRY_LOCKED_SECONDS)
            return
        try:
            await self.processor.process(job.tenant_id, job.conversation_id)
        finally:
            await self.queue.unlock(job.key, token)
