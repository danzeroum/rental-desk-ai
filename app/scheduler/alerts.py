"""Alertas ao operador (SPEC seção 10): fila acumulada, IA fora, rejeições do validador,
disco > 80%, imobiliária sem mensagens, datas vencidas, qualidade do número e backup.

A cada rodada, um único e-mail com os alertas novos; o mesmo alerta (mesma chave e
mesmo texto) não se repete por 12 h. Tudo também vai para o log (`alerta_operacional`).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.mailer import Mailer, MailError
from app.core.logging import get_logger
from app.core.rls import global_session, tenant_session
from app.domain.health import Alert, Level, PlatformInput, assess, assess_platform
from app.models import Tenant
from app.workers.brokers import operator_emails
from app.workers.health import collect, dead_jobs_by_tenant
from app.workers.queue import STREAM_NORMAL, STREAM_PRIORITY

ALERT_EVERY_SECONDS: Final = 300
REPEAT_AFTER_SECONDS: Final = 12 * 3600
HEARTBEAT_KEY: Final = "rd:heartbeat:backup"
log = get_logger(__name__)
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AlertService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        ops_engine: AsyncEngine,
        redis: Redis,
        mailer: Mailer,
        public_base_url: str,
        disk_path: str = "/",
        clock: Clock = _utcnow,
    ) -> None:
        self.sessions, self.ops_engine, self.redis, self.mailer = (
            sessions,
            ops_engine,
            redis,
            mailer,
        )
        self.panel_url = f"{public_base_url.rstrip('/')}/operador"
        self.disk_path, self.clock = disk_path, clock

    async def platform(self) -> PlatformInput:
        backlog = sum([int(await self.redis.xlen(s)) for s in (STREAM_NORMAL, STREAM_PRIORITY)])
        usage = shutil.disk_usage(self.disk_path)
        raw = await self.redis.get(HEARTBEAT_KEY)
        beat = json.loads(raw) if raw else {}
        at = datetime.fromtimestamp(int(beat["at"]), tz=UTC) if beat.get("at") else None
        return PlatformInput(backlog, usage.used / usage.total, at, bool(beat.get("remote")))

    async def _tenant_ids(self) -> list[uuid.UUID]:
        async with self.ops_engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT id FROM tenant WHERE status IN ('active', 'shadow') ORDER BY name")
            )
            return [row[0] for row in rows]

    async def tenant_alerts(self, now: datetime) -> list[Alert]:
        dead = await dead_jobs_by_tenant(self.redis)
        alerts = []
        for tenant_id in await self._tenant_ids():
            async with tenant_session(self.sessions, tenant_id) as session:
                tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                continue
            snap = await collect(
                self.sessions, tenant, dead_jobs=dead.get(str(tenant_id), 0), now=now
            )
            health = assess(snap.data, now)
            if health.level is not Level.OK:
                reasons = "; ".join(health.reasons)
                alerts.append(
                    Alert(f"tenant:{tenant_id}", health.level, f"{tenant.name}: {reasons}")
                )
        return alerts

    async def run(self) -> int:
        """Uma rodada; devolve quantos alertas novos foram avisados."""
        now = self.clock()
        alerts = assess_platform(await self.platform(), now) + await self.tenant_alerts(now)
        fresh = [a for a in alerts if await self._is_new(a)]
        for alert in alerts:
            emit = log.warning if alert in fresh else log.debug
            emit("alerta_operacional", key=alert.key, level=str(alert.level), text=alert.text)
        if fresh:
            await self._notify(fresh)
        return len(fresh)

    async def _is_new(self, alert: Alert) -> bool:
        digest = hashlib.sha256(f"{alert.level}|{alert.text}".encode()).hexdigest()[:16]
        key = f"rd:alert:{alert.key}:{digest}"
        return bool(await self.redis.set(key, "1", nx=True, ex=REPEAT_AFTER_SECONDS))

    async def _notify(self, alerts: list[Alert]) -> None:
        async with global_session(self.sessions) as session:
            emails = await operator_emails(session)
        errors = sum(a.level is Level.ERROR for a in alerts)
        suffix = f" — {errors} erro(s)" if errors else ""
        subject = f"[Rental Desk] {len(alerts)} alerta(s){suffix}"
        lines = [f"- [{a.level}] {a.text}" for a in alerts]
        body = "\n".join(lines) + f"\n\nPainel: {self.panel_url}\n"
        for email in emails:
            try:
                await self.mailer.send(email, subject, body)
            except MailError:
                log.warning("alerta_email_falhou")
