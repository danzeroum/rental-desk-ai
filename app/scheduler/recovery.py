"""Recuperação de mensagens paradas (teste de falha, M8).

Se o Redis cair depois de a mensagem ser gravada, o webhook responde erro, a Meta
reenvia, e o reenvio é reconhecido como duplicado (não agenda de novo). Esta varredura
reagenda toda conversa com mensagem de hóspede ainda não processada há mais de 1 min.
Agendar é idempotente (debounce) e o processador decide o que fazer (pausa, PARAR...).
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.logging import get_logger
from app.workers.queue import WorkQueue, conversation_key

RECOVERY_EVERY_SECONDS: Final = 60
STUCK_SECONDS: Final = 60
MAX_PER_ROUND: Final = 500
log = get_logger(__name__)


async def reschedule_stuck(ops_engine: AsyncEngine, queue: WorkQueue) -> int:
    async with ops_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT DISTINCT tenant_id, conversation_id FROM message"
                    " WHERE direction = 'in' AND processed_at IS NULL"
                    " AND created_at < now() - make_interval(secs => :stuck) LIMIT :limit"
                ),
                {"stuck": STUCK_SECONDS, "limit": MAX_PER_ROUND},
            )
        ).all()
    for tenant_id, conversation_id in rows:
        await queue.schedule(conversation_key(tenant_id, conversation_id))
    if rows:
        log.warning("mensagens_paradas_reagendadas", conversations=len(rows))
    return len(rows)
