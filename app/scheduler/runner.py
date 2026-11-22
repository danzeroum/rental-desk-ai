"""Processo `scheduler`: tarefas periódicas (escalonamento; no M7, lembrete, relatório e retenção).

Cada tarefa roda sob trava no Redis, para que dois schedulers não executem a mesma
tarefa ao mesmo tempo. A lista de imobiliárias com trabalho pendente vem do papel
`rentaldesk_ops` (consulta global, SPEC seção 6); o trabalho em si roda por
imobiliária, com o papel da aplicação e RLS.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.logging import clear_context, get_logger

LOCK_PREFIX: Final = "rd:scheduler:lock:"
log = get_logger(__name__)

JobFn = Callable[[], Awaitable[int]]


@dataclass
class Job:
    name: str
    every_seconds: float
    run: JobFn
    last_run: float = field(default=-1e18)


async def tenants_with_open_handoffs(ops_engine: AsyncEngine) -> list[uuid.UUID]:
    async with ops_engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT DISTINCT tenant_id FROM handoff WHERE status = 'open'")
        )
        return [row[0] for row in rows]


class Scheduler:
    def __init__(
        self, redis: Redis, jobs: list[Job], *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.redis, self.jobs, self.clock = redis, jobs, clock

    async def tick(self) -> list[str]:
        """Roda as tarefas vencidas; devolve os nomes das que rodaram."""
        ran = []
        for job in self.jobs:
            if self.clock() - job.last_run < job.every_seconds:
                continue
            job.last_run = self.clock()
            lock = await self.redis.set(
                f"{LOCK_PREFIX}{job.name}", "1", nx=True, ex=max(int(job.every_seconds), 1)
            )
            if not lock:
                continue
            clear_context()
            try:
                actions = await job.run()
                log.info("tarefa_agendada_concluida", job=job.name, actions=actions)
            except Exception:
                log.exception("tarefa_agendada_falhou", job=job.name)
            ran.append(job.name)
        return ran

    async def run(self, stop: asyncio.Event, *, interval: float = 5.0) -> None:
        while not stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                continue
