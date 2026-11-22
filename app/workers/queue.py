"""Fila de processamento sobre Redis (D002): debounce, prioridade, limite anti-abuso.

- **Debounce (RF05):** cada mensagem nova adia o processamento da conversa para
  `quiet` segundos depois dela, sem passar de `max` segundos desde a primeira do
  lote. Um "pump" move as conversas vencidas para os streams de trabalho.
- **Streams:** `jobs:priority` (primeira mensagem de contato novo, RF20) e
  `jobs:normal`, consumidos por um grupo; mensagens não confirmadas são
  retomadas por outro worker depois de `RECLAIM_IDLE_MS`.
- **Limite anti-abuso (RF20):** janela deslizante de 60 s por contato; acima do
  limite, silêncio de 10 min e um único aviso educado.
- **Trava por conversa:** um lote por vez; outro lote que chega no meio é adiado.

Todas as operações compostas são scripts Lua (atômicas). O relógio é injetável.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from redis.asyncio import Redis

PREFIX: Final = "rd"
DUE: Final = f"{PREFIX}:debounce:due"
FIRST: Final = f"{PREFIX}:debounce:first"
PRIORITY_SET: Final = f"{PREFIX}:debounce:priority"
STREAM_PRIORITY: Final = f"{PREFIX}:jobs:priority"
STREAM_NORMAL: Final = f"{PREFIX}:jobs:normal"
DEAD_LETTER: Final = f"{PREFIX}:jobs:dead"
DEAD_LETTER_MAX: Final = 10_000
GROUP: Final = "workers"
RECLAIM_IDLE_MS: Final = 60_000
MAX_DELIVERIES: Final = 3
LOCK_TTL_MS: Final = 120_000
RETRY_LOCKED_SECONDS: Final = 2.0

Clock = Callable[[], float]

_SCHEDULE = """
local first = redis.call('HGET', KEYS[2], ARGV[1])
if not first then
  first = ARGV[2]
  redis.call('HSET', KEYS[2], ARGV[1], ARGV[2])
end
local due = math.min(tonumber(ARGV[2]) + tonumber(ARGV[3]), tonumber(first) + tonumber(ARGV[4]))
redis.call('ZADD', KEYS[1], due, ARGV[1])
if ARGV[5] == '1' then redis.call('SADD', KEYS[3], ARGV[1]) end
return tostring(due)
"""

_PUMP = """
local items = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2]))
for _, member in ipairs(items) do
  redis.call('ZREM', KEYS[1], member)
  redis.call('HDEL', KEYS[2], member)
  local stream = KEYS[5]
  if redis.call('SREM', KEYS[3], member) == 1 then stream = KEYS[4] end
  redis.call('XADD', stream, '*', 'kind', 'conversation', 'key', member)
end
return #items
"""

_RATE = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', tonumber(ARGV[1]) - 60)
redis.call('ZADD', KEYS[1], ARGV[1], ARGV[2])
redis.call('EXPIRE', KEYS[1], 120)
if redis.call('EXISTS', KEYS[2]) == 1 then return 1 end
if redis.call('ZCARD', KEYS[1]) > tonumber(ARGV[3]) then
  redis.call('SET', KEYS[2], '1', 'EX', tonumber(ARGV[4]))
  return 2
end
return 0
"""

_UNLOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


class RateDecision(StrEnum):
    OK = "ok"
    SILENCED = "silenced"  # já em silêncio: guarda a mensagem e não responde
    JUST_SILENCED = "just_silenced"  # passou do limite agora: um aviso educado e silêncio


@dataclass(frozen=True)
class Job:
    stream: str
    job_id: str
    kind: str
    key: str
    deliveries: int = 1

    @property
    def tenant_id(self) -> uuid.UUID:
        return uuid.UUID(self.key.split(":", 1)[0])

    @property
    def conversation_id(self) -> uuid.UUID:
        return uuid.UUID(self.key.split(":", 1)[1])


def conversation_key(tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    return f"{tenant_id}:{conversation_id}"


class WorkQueue:
    def __init__(
        self,
        redis: Redis,
        *,
        quiet_seconds: float = 6.0,
        max_seconds: float = 20.0,
        rate_limit_per_minute: int = 15,
        silence_seconds: int = 600,
        clock: Clock = time.time,
    ) -> None:
        self.redis = redis
        self.quiet, self.max = quiet_seconds, max_seconds
        self.rate_limit, self.silence = rate_limit_per_minute, silence_seconds
        self.clock = clock
        self._schedule = redis.register_script(_SCHEDULE)
        self._pump = redis.register_script(_PUMP)
        self._rate = redis.register_script(_RATE)
        self._unlock = redis.register_script(_UNLOCK)

    async def ensure_groups(self) -> None:
        for stream in (STREAM_PRIORITY, STREAM_NORMAL):
            try:
                await self.redis.xgroup_create(stream, GROUP, id="0", mkstream=True)
            except Exception as exc:  # BUSYGROUP: o grupo já existe
                if "BUSYGROUP" not in str(exc):
                    raise

    # --- Debounce ------------------------------------------------------------------

    async def schedule(
        self, key: str, *, priority: bool = False, delay: float | None = None
    ) -> float:
        """Agenda (ou adia) o processamento da conversa; devolve o instante previsto."""
        quiet = self.quiet if delay is None else delay
        due = await self._schedule(
            keys=[DUE, FIRST, PRIORITY_SET],
            args=[key, repr(self.clock()), repr(quiet), repr(self.max), "1" if priority else "0"],
        )
        return float(due)

    async def pump(self, limit: int = 100) -> int:
        """Move para os streams as conversas cujo prazo venceu; devolve quantas."""
        moved = await self._pump(
            keys=[DUE, FIRST, PRIORITY_SET, STREAM_PRIORITY, STREAM_NORMAL],
            args=[repr(self.clock()), str(limit)],
        )
        return int(moved)

    async def enqueue(self, kind: str, key: str, *, priority: bool = False) -> None:
        """Trabalho imediato, sem debounce (ex.: aviso do limite anti-abuso)."""
        await self.redis.xadd(
            STREAM_PRIORITY if priority else STREAM_NORMAL, {"kind": kind, "key": key}
        )

    # --- Consumo ---------------------------------------------------------------------

    async def read(self, consumer: str, *, count: int = 10, block_ms: int = 1000) -> list[Job]:
        """Prioridade primeiro; depois a fila normal (com espera)."""
        jobs = await self._read(consumer, [STREAM_PRIORITY], count, None)
        if jobs:
            return jobs
        return await self._read(consumer, [STREAM_PRIORITY, STREAM_NORMAL], count, block_ms)

    async def _read(
        self, consumer: str, streams: list[str], count: int, block_ms: int | None
    ) -> list[Job]:
        response: Any = await self.redis.xreadgroup(
            GROUP, consumer, dict.fromkeys(streams, ">"), count=count, block=block_ms
        )
        return [
            self._job(stream, job_id, fields)
            for stream, items in response or []
            for job_id, fields in items
        ]

    @staticmethod
    def _job(stream: Any, job_id: Any, fields: dict[Any, Any], deliveries: int = 1) -> Job:
        def text(value: Any) -> str:
            return value.decode() if isinstance(value, bytes) else str(value)

        data = {text(k): text(v) for k, v in fields.items()}
        return Job(
            text(stream), text(job_id), data.get("kind", ""), data.get("key", ""), deliveries
        )

    async def ack(self, job: Job) -> None:
        await self.redis.xack(job.stream, GROUP, job.job_id)
        await self.redis.xdel(job.stream, job.job_id)

    async def reclaim(self, consumer: str, *, min_idle_ms: int = RECLAIM_IDLE_MS) -> list[Job]:
        """Retoma trabalhos de workers que caíram; após `MAX_DELIVERIES` vão para a fila morta."""
        reclaimed: list[Job] = []
        for stream in (STREAM_PRIORITY, STREAM_NORMAL):
            pending: Any = await self.redis.xpending_range(
                stream, GROUP, min="-", max="+", count=50
            )
            for item in pending:
                if item["time_since_delivered"] < min_idle_ms:
                    continue
                claimed: Any = await self.redis.xclaim(
                    stream, GROUP, consumer, min_idle_ms, [item["message_id"]]
                )
                for job_id, fields in claimed:
                    job = self._job(stream, job_id, fields, int(item["times_delivered"]) + 1)
                    if job.deliveries > MAX_DELIVERIES:
                        await self.redis.xadd(
                            DEAD_LETTER,
                            {"kind": job.kind, "key": job.key, "from": stream},
                            maxlen=DEAD_LETTER_MAX, approximate=True,
                        )  # fmt: skip
                        await self.ack(job)
                    else:
                        reclaimed.append(job)
        return reclaimed

    # --- Trava por conversa --------------------------------------------------------

    async def lock(self, key: str) -> str | None:
        token = uuid.uuid4().hex
        acquired = await self.redis.set(f"{PREFIX}:lock:{key}", token, nx=True, px=LOCK_TTL_MS)
        return token if acquired else None

    async def unlock(self, key: str, token: str) -> None:
        await self._unlock(keys=[f"{PREFIX}:lock:{key}"], args=[token])

    # --- Limite anti-abuso ---------------------------------------------------------

    async def register_inbound(
        self, tenant_id: uuid.UUID, contact_id: uuid.UUID, message_id: str
    ) -> RateDecision:
        base = f"{PREFIX}:rl:{tenant_id}:{contact_id}"
        result = await self._rate(
            keys=[base, f"{base}:silenced"],
            args=[repr(self.clock()), message_id, str(self.rate_limit), str(self.silence)],
        )
        return {0: RateDecision.OK, 1: RateDecision.SILENCED, 2: RateDecision.JUST_SILENCED}[
            int(result)
        ]

    async def is_silenced(self, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> bool:
        return bool(await self.redis.exists(f"{PREFIX}:rl:{tenant_id}:{contact_id}:silenced"))
