"""Sincronização de calendários iCal (RF26).

A cada 15 min, cada link é lido e os bloqueios dele são trocados pelos do calendário
(origem `ical`). O endereço fica cifrado (costuma ter token). Três falhas seguidas
geram alerta ao gestor (e-mail) e ao operador (saúde da imobiliária). Quando todas as
casas da planilha aprovada têm calendário sincronizado com sucesso, a sincronização
conta como "datas atualizadas" (RF13).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.http_fetch import Fetcher, FetchError
from app.adapters.mailer import Mailer, MailError
from app.core.audit import append_audit
from app.core.config import TIMEZONE
from app.core.crypto import Keyring
from app.core.logging import bind_context, get_logger
from app.core.rls import tenant_session
from app.domain.ical import CalendarError, parse_ics
from app.models import Block, CalendarFeed, Property, Tenant
from app.models.enums import BlockSource
from app.workers.brokers import manager_emails

SYNC_EVERY_SECONDS: Final = 15 * 60
ALERT_AFTER_FAILURES: Final = 3
FRESH_WINDOW: Final = timedelta(minutes=30)
LABELS: Final = (
    ("airbnb", "Airbnb"),
    ("booking", "Booking"),
    ("vrbo", "Vrbo"),
    ("homeaway", "Vrbo"),
)
log = get_logger(__name__)
Clock = Callable[[], datetime]


class FeedError(ValueError):
    """Mensagem para o painel."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def feed_context(tenant_id: uuid.UUID) -> str:
    return f"ical:{tenant_id}"


def describe(url: str) -> tuple[str, str]:
    """(origem, dica) a partir do endereço; a dica é só o domínio (o token fica de fora)."""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise FeedError("cole o endereço completo do calendário (começa com https://)")
    host = parts.hostname.lower()
    label = next((name for key, name in LABELS if key in host), "Outro")
    return label, host[:80]


class CalendarService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        fetcher: Fetcher,
        keyring: Keyring,
        mailer: Mailer,
        public_base_url: str,
        clock: Clock = _utcnow,
    ) -> None:
        self.sessions, self.fetcher, self.keyring, self.mailer = sessions, fetcher, keyring, mailer
        self.panel_url = f"{public_base_url.rstrip('/')}/painel/calendarios"
        self.clock = clock

    # --- Cadastro -------------------------------------------------------------------

    async def add(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        code: str,
        url: str,
    ) -> CalendarFeed:
        label, hint = describe(url)
        tenant = await session.get(Tenant, tenant_id)
        known = set(
            (
                await session.execute(
                    select(Property.code).where(
                        Property.snapshot_id == (tenant.active_snapshot_id if tenant else None)
                    )
                )
            ).scalars()
        )
        if code not in known:
            raise FeedError(f"a casa {code} não está na planilha aprovada")
        feed = CalendarFeed(
            tenant_id=tenant_id, property_code=code, label=label, url_hint=hint,
            url_enc=self.keyring.encrypt(url.strip(), context=feed_context(tenant_id)),
            created_by=user_id,
        )  # fmt: skip
        session.add(feed)
        await session.flush()
        await append_audit(
            session, tenant_id=tenant_id, event_type="calendar_feed_added",
            payload={"feed_id": str(feed.id), "codigo": code, "origem": label,
                     "user_id": str(user_id) if user_id else None},
        )  # fmt: skip
        return feed

    # --- Sincronização ----------------------------------------------------------------

    async def sync_tenant(self, tenant_id: uuid.UUID) -> int:
        """Sincroniza todos os links da imobiliária; devolve quantos deram certo."""
        bind_context(tenant_id=str(tenant_id))
        async with tenant_session(self.sessions, tenant_id) as session:
            feeds = list((await session.execute(select(CalendarFeed.id))).scalars())
        ok = sum([await self.sync_feed(tenant_id, feed_id) for feed_id in feeds])
        if feeds:
            await self._renew_freshness(tenant_id)
        return ok

    async def sync_feed(self, tenant_id: uuid.UUID, feed_id: uuid.UUID) -> bool:
        now = self.clock()
        async with tenant_session(self.sessions, tenant_id) as session:
            feed = await session.get(CalendarFeed, feed_id)
            if feed is None:
                return False
            url = self.keyring.decrypt(feed.url_enc, context=feed_context(tenant_id))
        try:
            events = parse_ics((await self.fetcher.fetch(url)).decode("utf-8", errors="replace"))
        except (FetchError, CalendarError) as exc:
            await self._failed(tenant_id, feed_id, str(exc), now)
            return False
        today = now.astimezone(TIMEZONE).date()
        upcoming = {e.uid: e for e in events if e.end > today}
        async with tenant_session(self.sessions, tenant_id) as session:
            feed = await session.get(CalendarFeed, feed_id, with_for_update=True)
            if feed is None:
                return False
            await session.execute(delete(Block).where(Block.calendar_feed_id == feed_id))
            session.add_all(
                Block(
                    tenant_id=tenant_id,
                    property_code=feed.property_code,
                    start_date=e.start,
                    end_date=e.end,
                    source=BlockSource.ICAL,
                    external_uid=f"{feed_id}:{uid}"[:255],
                    calendar_feed_id=feed_id,
                )
                for uid, e in upcoming.items()
            )
            feed.last_attempt_at = feed.last_success_at = now
            feed.consecutive_failures, feed.last_error, feed.alerted_at = 0, None, None
            feed.events = len(upcoming)
        log.info("calendario_sincronizado", feed_id=str(feed_id), events=len(upcoming))
        return True

    async def _failed(
        self, tenant_id: uuid.UUID, feed_id: uuid.UUID, error: str, now: datetime
    ) -> None:
        async with tenant_session(self.sessions, tenant_id) as session:
            feed = await session.get(CalendarFeed, feed_id, with_for_update=True)
            if feed is None:
                return
            feed.last_attempt_at, feed.last_error = now, error[:300]
            feed.consecutive_failures += 1
            alert = feed.consecutive_failures >= ALERT_AFTER_FAILURES and feed.alerted_at is None
            if alert:
                feed.alerted_at = now
                await append_audit(
                    session, tenant_id=tenant_id, event_type="calendar_feed_failing",
                    payload={"feed_id": str(feed_id), "codigo": feed.property_code,
                             "falhas": feed.consecutive_failures, "erro": error[:300]},
                )  # fmt: skip
            code, label = feed.property_code, feed.label
            emails = await manager_emails(session) if alert else []
        log.warning("calendario_falhou", feed_id=str(feed_id), error=error[:120])
        for email in emails:
            try:
                await self.mailer.send(
                    email, f"Calendário da {code} sem sincronizar",
                    f"O calendário {label} da {code} falhou {ALERT_AFTER_FAILURES} vezes seguidas: "
                    f"{error}.\nEnquanto isso, os bloqueios dele podem estar desatualizados.\n"
                    f"Confira o link: {self.panel_url}\n",
                )  # fmt: skip
            except MailError:
                log.warning("alerta_calendario_nao_enviado")

    async def _renew_freshness(self, tenant_id: uuid.UUID) -> None:
        """RF13: só quando toda casa aprovada tem calendário e todos acabaram de sincronizar."""
        now = self.clock()
        async with tenant_session(self.sessions, tenant_id) as session:
            tenant = await session.get(Tenant, tenant_id, with_for_update=True)
            if tenant is None or tenant.active_snapshot_id is None:
                return
            codes = set(
                (
                    await session.execute(
                        select(Property.code).where(
                            Property.snapshot_id == tenant.active_snapshot_id
                        )
                    )
                ).scalars()
            )
            feeds = list((await session.execute(select(CalendarFeed))).scalars())
            covered = {f.property_code for f in feeds}
            fresh = all(
                f.last_success_at and now - f.last_success_at <= FRESH_WINDOW for f in feeds
            )
            if not codes or not codes <= covered or not fresh:
                return
            tenant.availability_updated_at = now
            await append_audit(
                session, tenant_id=tenant_id, event_type="availability_confirmed",
                payload={"via": "ical", "at": now.isoformat(), "feeds": len(feeds)},
            )  # fmt: skip
