"""Teste de saúde leve dos processos `worker` e `scheduler` (usado pelo Docker).

    python -m app.core.healthcheck worker

Só carrega configuração e Redis: importar o processo inteiro (SDK de IA, motor) levava
segundos e, com o worker ocupado, estourava o limite de 5 s do Docker (falso "unhealthy").
"""

from __future__ import annotations

import asyncio
import sys

from app.core.config import get_settings
from app.core.redis import create_redis
from app.core.service import is_alive

SERVICES = frozenset({"worker", "scheduler"})


async def check(service: str) -> int:
    redis = create_redis(get_settings())
    try:
        return 0 if await is_alive(redis, service) else 1
    finally:
        await redis.aclose()


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in SERVICES:
        print("uso: python -m app.core.healthcheck worker|scheduler", file=sys.stderr)  # noqa: T201
        return 2
    return asyncio.run(check(argv[0]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
