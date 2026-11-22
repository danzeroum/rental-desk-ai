"""Simulador do painel (RF28, passo 4): o gestor conversa com o assistente como se fosse um
hóspede, com os dados da imobiliária, sem WhatsApp e sem gravar conversa.

O estado da conversa simulada fica no Redis por 2 h (por pessoa). Cada mensagem gera um
evento `simulator_used` no `audit_log` (sem o texto), que conta como progresso da implantação.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import append_audit
from app.core.rls import tenant_session
from app.domain.prompts import HistoryItem
from app.domain.state import load_state
from app.models import Tenant
from app.workers.engine import ConversationEngine
from app.workers.pipeline import HISTORY_LIMIT, build_turn_context

TTL_SECONDS: Final = 2 * 3600
MAX_TEXT: Final = 1000
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Exchange:
    guest: str
    reply: str


class Simulator:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        engine: ConversationEngine,
        redis: Redis,
        public_base_url: str,
        clock: Clock = _utcnow,
    ) -> None:
        self.sessions, self.engine, self.redis = sessions, engine, redis
        self.public_base_url, self.clock = public_base_url.rstrip("/"), clock

    @staticmethod
    def _key(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
        return f"rd:sim:{tenant_id}:{user_id}"

    async def transcript(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Exchange]:
        raw = await self.redis.get(self._key(tenant_id, user_id))
        stored: dict[str, Any] = json.loads(raw) if raw else {}
        return [Exchange(e["guest"], e["reply"]) for e in stored.get("exchanges", [])]

    async def reset(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self.redis.delete(self._key(tenant_id, user_id))

    async def say(self, tenant_id: uuid.UUID, user_id: uuid.UUID, text: str) -> Exchange:
        text = text.strip()[:MAX_TEXT]
        key = self._key(tenant_id, user_id)
        raw = await self.redis.get(key)
        stored: dict[str, Any] = json.loads(raw) if raw else {"state": None, "exchanges": []}
        history = [
            item
            for e in stored["exchanges"]
            for item in (HistoryItem(True, e["guest"]), HistoryItem(False, e["reply"]))
        ][-HISTORY_LIMIT:]
        async with tenant_session(self.sessions, tenant_id, user_id=user_id) as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                raise LookupError("imobiliária não encontrada")
            context = await build_turn_context(
                session, tenant, now=self.clock(), public_base_url=self.public_base_url,
                state=load_state(stored["state"]), history=history, new_text=text,
                first_response=not stored["exchanges"],
            )  # fmt: skip
        result = await self.engine.run(context)
        exchange = Exchange(text, result.text)
        stored["state"] = result.state.to_json()
        stored["exchanges"] = [*stored["exchanges"], {"guest": text, "reply": result.text}][-20:]
        await self.redis.set(key, json.dumps(stored, ensure_ascii=False), ex=TTL_SECONDS)
        async with tenant_session(self.sessions, tenant_id, user_id=user_id) as session:
            await append_audit(
                session, tenant_id=tenant_id, event_type="simulator_used",
                payload={"user_id": str(user_id),
                         "handoff": result.handoff.reason if result.handoff else None},
            )  # fmt: skip
        return exchange
