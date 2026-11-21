"""Laço de processo de longa duração (worker, scheduler) com heartbeat e parada limpa.

Cada processo grava `heartbeat:<serviço>:<host>` no Redis com expiração; o
healthcheck do contêiner e o painel do operador leem essa chave.
"""

from __future__ import annotations

import asyncio
import signal
import socket
import sys
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Final

from redis.asyncio import Redis

from app import __version__
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.redis import create_redis

HEARTBEAT_INTERVAL_SECONDS: Final = 10
HEARTBEAT_TTL_SECONDS: Final = 3 * HEARTBEAT_INTERVAL_SECONDS

Tick = Callable[[Redis], Awaitable[None]]
Background = Callable[[asyncio.Event], Coroutine[Any, Any, None]]

log = get_logger(__name__)


def heartbeat_key(service: str, host: str | None = None) -> str:
    return f"heartbeat:{service}:{host or socket.gethostname()}"


async def beat(redis: Redis, service: str) -> None:
    await redis.set(heartbeat_key(service), str(int(time.time())), ex=HEARTBEAT_TTL_SECONDS)


async def is_alive(redis: Redis, service: str) -> bool:
    return bool(await redis.exists(heartbeat_key(service)))


async def run_service(
    service: str,
    tick: Tick | None = None,
    *,
    settings: Settings | None = None,
    stop: asyncio.Event | None = None,
    background: Background | None = None,
) -> None:
    """Executa `tick` em laço até SIGTERM/SIGINT, mantendo o heartbeat em dia.

    `background` (ex.: o laço do worker) roda em paralelo e recebe o mesmo evento de parada;
    se ele falhar, o processo para (o Docker reinicia o contêiner).
    """
    settings = settings or get_settings()
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    redis = create_redis(settings)
    log.info("servico_iniciado", service=service, version=__version__)
    task: asyncio.Task[None] | None = (
        asyncio.create_task(background(stop)) if background is not None else None
    )
    if task is not None:
        task.add_done_callback(lambda _: stop.set())
    try:
        while not stop.is_set():
            await beat(redis, service)
            if tick is not None:
                await tick(redis)
            try:
                await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            except TimeoutError:
                continue
    finally:
        if task is not None:
            stop.set()
            await asyncio.gather(task, return_exceptions=True)
            if not task.cancelled() and task.exception() is not None:
                log.error(
                    "servico_falhou", service=service, error_type=type(task.exception()).__name__
                )
        await redis.delete(heartbeat_key(service))
        await redis.aclose()
        log.info("servico_encerrado", service=service)


async def _healthcheck(service: str) -> int:
    redis = create_redis(get_settings())
    try:
        return 0 if await is_alive(redis, service) else 1
    finally:
        await redis.aclose()


def main(service: str, background: Background | None = None) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if len(sys.argv) > 1 and sys.argv[1] == "healthcheck":
        sys.exit(asyncio.run(_healthcheck(service)))
    asyncio.run(run_service(service, settings=settings, background=background))
